# Keychron Assist para Linux (não oficial)

O Keychron Launcher (`https://launcher.keychron.com`) tem um recurso chamado
**Quick Start → Launch APP**, que abre programas com uma tecla do teclado.
No Windows e no macOS esse recurso depende do utilitário **Keychron Assist**,
que não existe para Linux. Este projeto implementa um substituto livre.

Documentação de retomada (o que está pronto, o que falta, decisões e
armadilhas): [`docs/estado-atual.md`](docs/estado-atual.md).

Status: **funcional, sem root** no Fedora 44 (GNOME 50, Wayland). Lista de apps,
busca, ícones, gravação da binding e disparo pela tecla física foram validados.
Debian/Ubuntu: pacotes e keymap conferidos, falta o teste em sessão gráfica
(roteiro em [`docs/vm-testing.pt-BR.md`](docs/vm-testing.pt-BR.md)).

| distro | GNOME/Wayland | slots utilizáveis (modo GNOME) | situação |
|---|---|---|---|
| Fedora 44 | 50.6 | **4** (F16–F19) | testado com teclado real |
| Debian 13 | 48 | **3** (F16–F18) | pacotes e XKB conferidos |
| Ubuntu 24.04 LTS | 46 | **3** (F16–F18) | pacotes e XKB conferidos |

O número de slots depende do `xkeyboard-config` (detalhe abaixo), não do
projeto.

## Como o Launcher conversa com o Assist

Tudo foi descoberto lendo o bundle JS do site (`main.*.js`). O protocolo é
simples e não tem autenticação:

```
ws://localhost:40003
```

Cada mensagem é JSON:

```json
{"evt_type": "QuickStart" | "Common" | "SysCmd",
 "evt_data": {"cmd": "<comando>", "data": <payload>}}
```

| comando | direção | resposta esperada |
|---|---|---|
| `GetVersion` (Common) | site → app | `{"cmd":"GetVersion","data":{"version":"1.1.6"}}` |
| `Get` | site → app | streaming: `FetchStart{count}`, N × `Fetch{data:{name,path,icon,icon_path}}`, `FetchEnd` |
| `AddKeyBind` / `UpdateKeyBind` | site → app | `"Success"` |
| `RemoveKeyBind` | site → app | `"Success"` |
| `GetKeyBinds` | site → app | `[{key, opt_type, opt_data:{name,path,icon,icon_path}}]` |
| `ClearKeyBind` | site → app | `"Success"` |
| `SpawnAppByPath` (SysCmd) | site → app | `true`/`false` |
| `AddApp` / `RemoveApp` / `SelectAppByNative` | site → app | `"Success"` / lista de apps |
| `SetReceiverVpidMap` / `ClearReceiverVpidMap` | site → app | ignorado no Linux |

Além disso o site cria um iframe invisível com
`launcherassist://?background=1` para "acordar" o app local — é esse esquema
que o instalador registra.

### O detalhe que torna tudo viável

O "slot" de quick start **é um keycode comum de QMK** que o site grava no
keymap via WebHID. O site usa:

- 5 primeiros slots: `KC_F16` … `KC_F20`;
- demais slots (até 50): keycodes vendor (`0x7F00 + i`, usage `0x1CC + i`),
  usados quando o firmware responde que suporta mais de 5 slots.

Como F16–F20 são teclas HID padrão, o Linux consegue capturá-las de duas
formas — nenhuma delas precisa decodificar relatórios vendor do teclado.

## Como a tecla chega no app: dois modos

**Modo GNOME (padrão, sem sudo).** O próprio GNOME captura as teclas como
atalhos personalizados e executa `kassist trigger F16`. Não é preciso ler
`/dev/input`: quem tem a permissão é o compositor, que já recebe as teclas.

Atenção ao XKB: a seção `inet` do keymap remapeia F13..F20, então a tecla
gravada como `F16` chega ao compositor como `XF86Launch7`. O instalador lê
`/usr/share/X11/xkb/symbols/inet` e registra o atalho no keyval **real** — é
isso que faz funcionar.

| slot | keycode | keyval (xkeyboard-config ≥ 2.47) | keyval (2.42, Debian 13/Ubuntu 24.04) |
|---|---|---|---|
| F16 | 194 | `XF86Launch7` | `XF86Launch7` |
| F17 | 195 | `XF86Launch8` | `XF86Launch8` |
| F18 | 196 | `XF86Launch9` | `XF86Launch9` |
| F19 | 197 | `F19` | **não definido** → slot inutilizável |
| F20 | 198 | `XF86AudioMicMute` | `XF86AudioMicMute` → conflita com o mute do microfone |

Por isso o instalador **detecta** o que o sistema oferece e registra só os
slots possíveis, em vez de assumir cinco. `XF86AudioMicMute` é sempre pulado
para não roubar o atalho do sistema.

**Modo udev (`./install.sh --udev`, pede sudo).** O daemon lê
`/dev/input/event*` direto. Captura **os 5 slots** (F16–F20, sem depender do
XKB) e é o caminho para os keycodes vendor (`A6`..`A55`). Exige a regra udev,
porque o kernel nega acesso a teclados de propósito (proteção contra keylogger).

## Instalação (sem sudo)

```bash
./install.sh
```

O script:

1. copia `kassist.py` para `~/.local/bin/kassist`;
2. instala e habilita o serviço de usuário `kassist.service`;
3. registra o handler de `launcherassist://` (`~/.local/share/applications`);
4. registra os atalhos do GNOME chamando `kassist trigger <slot>` (preserva os
   atalhos que já existirem e ignora slots sem keyval).

Dependências: `python3`, `gsettings`/`gio` (`libglib2.0-bin`), `xdg-mime`
(`xdg-utils`). O script avisa com o comando `apt`/`dnf`/`pacman` correto se
faltar algo.

**Uma exceção ao "sem sudo":** o *daemon* não precisa de root, mas o *site*
precisa de acesso a `/dev/hidraw*` para gravar o keycode no teclado (WebHID).
Se você nunca instalou as regras do QMK/VIA, rode uma vez:

```bash
sudo ./install.sh --rules      # só a regra udev, mantém o backend GNOME
```

Sem isso a lista de apps funciona, mas salvar a binding não tem efeito.

Para desfazer:

```bash
./install.sh --uninstall          # binário, serviço, handler e atalhos
```

As bindings continuam em `~/.config/keychron-assist-linux/binds.json`.

Depois abra `https://launcher.keychron.com` em um navegador **Chromium/Chrome**
(Firefox bloqueia `ws://` a partir de uma página `https`), conecte o teclado por
cabo e vá em **Quick Start**.

## Comandos

```bash
kassist run                     # serviço em foreground (padrão sem argumento)
kassist trigger F16             # abre o app do slot (usado pelos atalhos)
kassist doctor                  # diagnostica instalação/serviço/atalhos/bindings
kassist setup-shortcuts         # (re)registra os atalhos do GNOME
kassist remove-shortcuts        # remove os atalhos do GNOME
kassist list-apps               # lista os apps encontrados
kassist run --watch --sniff     # modo udev: mostra os eventos crus do teclado
kassist run --no-icons          # lista de apps sem ícones (mais rápido)
kassist protocol 'launcherassist://?background=1'   # handler de URI
```

Sem instalar nada:

```bash
python3 kassist.py list-apps
python3 tests/smoke.py                 # protocolo, sem navegador
python3 tools/selftest.py              # ambiente + instalação
python3 tools/selftest.py --simulate   # injeta a tecla com wtype (VM)
```

## Segurança

O serviço escuta só em `127.0.0.1`. Além disso:

- **Origem**: conexões WebSocket com cabeçalho `Origin` de outro site são
  recusadas com `403` — um site malicioso aberto no navegador não consegue
  falar com o daemon (era o risco real, já que `localhost` é alcançável por
  qualquer página).
- **Execução**: `SpawnAppByPath` só abre `.desktop` que o site listou ou que
  esteja num diretório de aplicações; `AddKeyBind` só aceita `App` (`.desktop`)
  ou `Url` (`http`/`https`). Nada de executar caminho arbitrário.

## Limitações

- **Slots.** F16–F20 são o conjunto que o firmware libera quando não anuncia os
  50 extras. O modo GNOME fica com 3 ou 4 slots (tabela acima); o modo udev tem
  os 5. Os slots `A6`..`A55` usam keycodes vendor que o GNOME não sabe
  expressar; precisariam do modo udev + mapeamento via `--sniff`.
- **A tecla não é "F16" de verdade.** F16/F17/F18 chegam como
  `XF86Launch7/8/9`. Se criar atalhos à mão no GNOME, use esses nomes.
- **Um atalho por slot.** Os atalhos do GNOME são globais: a tecla abre o app
  independentemente do perfil/camada ativa no teclado.
- **Receptor 2.4 GHz** (`SetReceiverVpidMap`) é ignorado; o pareamento
  teclado↔receptor do Assist original não é replicado. Use cabo.
- **Não oficial.** O protocolo pode mudar sem aviso em qualquer atualização do
  Launcher. `kassist doctor` e `tools/selftest.py` ajudam a localizar a quebra.
- **Privacidade (modo udev).** A regra udev dá ao seu usuário acesso de leitura
  ao teclado (igual a `keyd`/`kanata`). No modo GNOME isso não existe.

## Estrutura

```
kassist.py                          serviço + CLI (WS, apps, binds, atalhos, evdev)
install.sh                          instalador (--udev, --uninstall)
tests/smoke.py                      teste do protocolo com cliente WS próprio
tools/selftest.py                   diagnóstico de ambiente (e --simulate)
tools/keypeek.py                    mostra o keyval real que a tecla envia
docs/vm-testing.pt-BR.md            roteiro de teste em GNOME Boxes
packaging/kassist.service           unit systemd --user (template)
packaging/launcherassist.desktop    handler de launcherassist:// (template)
packaging/99-keychron-assist.rules  regra udev (só modo --udev)
```

## Licença

MIT (veja `LICENSE`). Projeto não oficial, sem vínculo com a Keychron.
