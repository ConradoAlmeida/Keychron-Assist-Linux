# Estado do projeto — ponto de retomada

> Última atualização: **2026-10-23**
> Máquina de referência: Fedora 44 Workstation, GNOME 50.6, Wayland,
> Python 3.14, teclado Keychron V6 Max (VID `3434`, PID `0960`).

Este documento existe para retomar o trabalho sem depender do histórico da
conversa. Ele registra o que está pronto, o que foi verificado de fato, o que
foi só conferido por leitura/repositório, e o que falta.

---

## 1. Resumo em uma frase

O **Keychron Launcher** (site) ganhou um assistente local para Linux — o
`kassist` — sem root: ele serve a lista de apps, guarda as bindings dos slots
de Quick Start e abre o app quando a tecla física é pressionada, via atalhos
personalizados do GNOME.

**Funciona hoje, validado com teclado real:** trocar de app no site, gravar a
binding e abrir o app pela tecla (F16 → Calculadora, depois Mixer, depois
Calculadora de novo).

---

## 2. Como retomar (5 minutos)

```bash
cd ~/development/keychron-assist-linux

git status                              # (ainda) não é repositório git
./kassist.py doctor                     # tudo deve dar OK
python3 tools/selftest.py               # 25 verificações, 0 falhas
systemctl --user status kassist         # serviço ativo
journalctl --user -u kassist -n 30      # conexões do site e lançamentos
```

Teste rápido sem abrir o site (usa a binding gravada):

```bash
~/.local/bin/kassist trigger F16        # abre o app do slot 1
```

Se mexer no código, reinstale para valer no serviço (o unit roda o binário de
`~/.local/bin`, não o do repo):

```bash
./install.sh
```

---

## 3. O que está instalado nesta máquina

Tudo em nível de usuário, nada em `/usr` (exceto a regra udev pré-existente,
que **não** foi instalada por este projeto).

| item | caminho |
|---|---|
| binário | `~/.local/bin/kassist` |
| serviço (systemd --user, enabled + active) | `~/.config/systemd/user/kassist.service` |
| handler de `launcherassist://` | `~/.local/share/applications/launcherassist.desktop` |
| atalhos F16–F19 | dconf, schema `org.gnome.settings-daemon.plugins.media-keys` |
| bindings | `~/.config/keychron-assist-linux/binds.json` |
| código | `~/development/keychron-assist-linux` |

Estado atual das bindings: `vp_id 875825504` → **F16 → Calculator**.

Desfazer:

```bash
./install.sh --uninstall                     # binário, serviço, handler, atalhos
# ou, sem o repo:
~/.local/bin/kassist remove-shortcuts
systemctl --user disable --now kassist
```

As bindings **não** são apagadas pelo `--uninstall` (ficam em `binds.json`).

---

## 4. Arquitetura (o mínimo para mexer)

```
Launcher (Chromium)  --ws://127.0.0.1:40003-->  kassist
   │  Get / AddKeyBind / RemoveKeyBind              │
   │  SpawnAppByPath                                 ├─ scans .desktop (106 apps)
   ▼                                                 ├─ binds.json
launcherassist://  ----handler-------------------->  └─ gio launch <app>
   (acorda o daemon se preciso)

tecla física ──> firmware (KC_F16) ──> kernel (keycode 194)
   ──> XKB (XF86Launch7) ──> atalho do GNOME ──> kassist trigger F16 ──> gio launch
```

- **Servidor**: WebSocket escrito à mão (stdlib), só em `127.0.0.1:40003`.
- **Protocolo**: JSON `{"evt_type","evt_data":{"cmd","data"}}`, documentado no
  `README.md` (tabela completa).
- **`Get`** é streaming: `FetchStart{count}` → N × `Fetch{data}` → `FetchEnd`.
- **`GetVersion`** responde `1.1.6` (é o que o site espera para habilitar o
  recurso; não é a versão deste projeto — esta é `0.1.0`).
- **Bindings** são gravadas por `vp_id` (identificador do dispositivo) e slot:
  `{"<vp_id>": {"F16": {"opt_type": "App", "opt_data": {...}}}}`.
- **Slots** são keycodes QMK comuns (F16–F20), não relatórios vendor.

---

## 5. Descobertas técnicas que não podem ser perdidas

### 5.1 O XKB renomeia F13–F20

A seção `inet` do keymap mapeia as teclas F13+ para keyvals "de sistema". O
atalho do GNOME tem que ser registrado no **keyval real**, não em `F16`:

| slot | keycode evdev | keyval (xkeyboard-config ≥ 2.47) | keyval (2.42: Debian 13 / Ubuntu 24.04) |
|---|---|---|---|
| F16 | 194 | `XF86Launch7` | `XF86Launch7` |
| F17 | 195 | `XF86Launch8` | `XF86Launch8` |
| F18 | 196 | `XF86Launch9` | `XF86Launch9` |
| F19 | 197 | `F19` | **não definido** → slot inutilizável |
| F20 | 198 | `XF86AudioMicMute` | `XF86AudioMicMute` |

`kassist.py` lê `/usr/share/X11/xkb/symbols/inet` (bloco `xkb_symbols "evdev"`)
em tempo de instalação e registra somente o que existe, pulando o que colide
com atalhos do sistema (`XF86AudioMicMute`).

Consequência prática: **Fedora 44 = 4 slots; Debian 13 / Ubuntu 24.04 = 3
slots** no modo GNOME; modo udev = 5 slots em qualquer distro.

### 5.2 O site precisa de acesso ao hidraw (WebHID)

Para gravar o keycode no teclado, o navegador usa WebHID → `/dev/hidraw*`.
Nesta máquina isso já funcionava por causa de regras **pré-existentes** do
usuário: `/etc/udev/rules.d/50-qmk.rules` e `70-keychron.rules` (MODE 0666 para
VID `3434`). Numa instalação limpa (VM), sem regra, o site lista os apps mas
**não salva** a binding.

O projeto agora tem `packaging/99-keychron-assist.rules` e o instalador:

```bash
sudo ./install.sh --rules     # instala só a regra (mantém backend GNOME)
```

O `doctor` e o `selftest` avisam quando não há nó hidraw gravável.

### 5.3 Permissões de entrada no Linux

- `/dev/input/event*` de teclados é `root:input 660`, **sem** uaccess.
- `/dev/hidraw*` só ganha ACL com regra (uaccess/udev).
- Por isso o modo padrão usa os **atalhos do compositor**: quem lê a tecla é o
  GNOME, que já tem permissão. O modo `--udev` (sudo) existe para os 5 slots e
  para os keycodes vendor futuros.

### 5.4 Origens aceitas no WebSocket

O daemon aceita `Origin`:

- `https://launcher.keychron.com`
- `https://www.launcher.keychron.com`
- `https://launcher.keychron.cn`

Sem `Origin` (clientes locais, testes) é aceito; com origem diferente → `403`.
Isso fecha o risco real: qualquer site aberto no navegador podia falar com
`localhost:40003`. Se o site mudar de domínio, os apps "desaparecem" e o
`journalctl` mostra `rejected origin:`.

### 5.5 Execução restrita

`SpawnAppByPath` só abre `.desktop` que o site listou ou que esteja num
diretório de aplicações (XDG/Flatpak/snap). `AddKeyBind` só aceita `App`
(`.desktop` existente) ou `Url` (`http`/`https`). Antes era possível pedir
qualquer caminho.

---

## 6. O que foi feito nesta rodada (refactor para distribuição)

Reescrita do `kassist.py` (901 linhas) mantendo o formato do `binds.json` e o
comportamento validado:

1. **CLI com subcomandos**: `run` (padrão), `trigger`, `doctor`,
   `setup-shortcuts`, `remove-shortcuts`, `list-apps`, `sniff`, `protocol`.
   Compatibilidade: `--protocol` ainda funciona; rodar sem argumento = `run`.
2. **Segurança**: validação de `Origin` e allowlist de execução (5.4/5.5).
3. **`doctor`**: python, serviço/porta (com tolerância de 3 s para o boot),
   handler de URI, atalhos por keyval, apps das bindings, backend evdev
   (opcional) e hidraw (opcional).
4. **`install.sh`**: detecção de dependências com dica `apt`/`dnf`/`pacman`,
   modos `--udev`, `--rules`, `--uninstall`, verificação final automática, e
   aviso se o hidraw não estiver acessível. `partOf=graphical-session.target`
   no unit; handler com caminho absoluto.
5. **`tools/selftest.py`**: relatório de ambiente (distro, sessão, navegador,
   atalhos, bindings, hidraw) e `--simulate SLOT`, que injeta o keyval real com
   `wtype` e confirma que um processo novo apareceu — testa a cadeia do
   compositor sem hardware.
6. **`tests/smoke.py`**: agora envia `Origin`, testa rejeição de origem
   estranha, confere `GetKeyBinds` por `vp_id`, recusa `SpawnAppByPath`
   arbitrário e limpa a binding de teste.
7. **Docs**: `README.md` reescrito (matriz de distros, slots, segurança,
   comandos), `docs/vm-testing.pt-BR.md` novo, `LICENSE` MIT.
8. **Removido**: `packaging/gnome-shortcuts.py` (virou `kassist
   setup-shortcuts` / `remove-shortcuts`).

### O que foi verificado de fato

- `./install.sh` do zero → serviço ativo, 4 atalhos, `doctor` 100% OK.
- `tests/smoke.py` contra instância isolada (porta 40006, `XDG_CONFIG_HOME`
  temporário): **PASS** em todas as checagens, incluindo `403` para origem
  estranha e `False` para `/usr/bin/true`.
- `tools/selftest.py`: **25 verificações, 0 falhas**.
- `kassist trigger F16` (fluxo do atalho) e `kassist protocol` funcionam.
- `--version`, `--help`, `doctor` com serviço recém-reiniciado (sem falso
  negativo).

### O que foi conferido só por repositório (containers Podman)

- Debian 13: `python3 3.13.5`, `libglib2.0-bin`, `xdg-utils`,
  `desktop-file-utils`, `zenity 4.1.90`, `wtype 0.4-3`, `chromium 156`.
- Ubuntu 24.04: os mesmos, com `chromium-browser` = wrapper do snap.
- XKB: `xkeyboard-config` 2.42 nas duas → **sem** `<FK19>`. Upstream só ganhou
  `<FK19> { [ F19 ] }` na **2.47** (Fedora 44 usa 2.47).

Isso ainda **não** é teste de sessão gráfica. É exatamente o que a VM cobre.

---

## 7. Pendências (em ordem sugerida)

### 7.1 Imediato — testar em Debian/Ubuntu (GNOME Boxes)

Roteiro completo: [`docs/vm-testing.pt-BR.md`](vm-testing.pt-BR.md).
Resumo: Ubuntu 24.04 e/ou Debian 13 GNOME, instalar deps, copiar o repo via
`http://10.0.2.2:8000` (host), `./install.sh`, `sudo ./install.sh --rules`,
redirecionar o Keychron em **Preferências → Devices & Shares → USB**, gravar a
binding no site e pressionar a tecla. Coletar `python3 tools/selftest.py >
~/selftest.txt`.

Pontos de atenção já mapeados:

- 3 slots no modo GNOME (F19/`<FK19>` ausente) — esperado, não é bug;
- Chromium no Ubuntu é snap: o `xdg-open` do `launcherassist://` passa pelo
  portal — é o item mais provável de falhar;
- sem a regra hidraw o site não salva (seção 5.2).

### 7.2 Empacotamento

- `.deb` (prioritário, já que Debian/Ubuntu é mandatório): binário em
  `/usr/bin/kassist`, unit em `/usr/lib/systemd/user/`, `.desktop` em
  `/usr/share/applications/` (com `Exec=kassist protocol %u`), regra udev
  **opcional** (subpacote ou `kassist --rules`), `%post` com
  `update-desktop-database`.
- `.rpm` + COPR (Fedora): mesma estrutura.
- `PKGBUILD` (Arch) — o público de teclado customizado é grande.
- Flatpak: **inviável** para o daemon (precisa abrir apps do host); documentar
  como não suportado.
- Os templates `packaging/kassist.service` e `launcherassist.desktop` usam
  `@EXEC@`; o empacotamento substitui por `kassist`.

### 7.3 Publicação

- `git init` + commit (repo ainda **não** é git; nada foi commitado).
- README em inglês (o PT pode virar `README.pt-BR.md`).
- CI (GitHub Actions): `python3 -m compileall`, smoke test com o daemon
  iniciado, `shellcheck` no `install.sh`.
- Aviso "não oficial, sem vínculo com a Keychron" (já no README).
- Se for divulgar: post curto no r/Keychron e r/linuxquestions; e-mail técnico
  para o suporte da Keychron oferecendo o projeto como referência.

### 7.4 Funcionalidades

- **Slots vendor `A6`..`A55`** (quando o firmware anuncia os 50 extras): modo
  udev + mapeamento via `--sniff`; hoje o site nem oferece.
- **2.4 GHz**: `SetReceiverVpidMap`/`ClearReceiverVpidMap` são ignorados; só
  cabo.
- **F19 em distros com xkeyboard-config < 2.47**: alternativa sem root seria
  um override em `~/.config/xkb`, mas isso sombreia o arquivo do sistema e
  quebra em atualização — decisão consciente de **não** fazer.
- **Fluxos não exercitados contra o site real** (revisar quando testar):
  `GetKeyBinds` com `vp_id` (assumimos filtro), `UpdateKeyBind` tratado como
  `AddKeyBind`, `ClearKeyBind` limpando o `vp_id` inteiro,
  `SelectAppByNative` (diálogo zenity → responde `AddApp`), `AddApp`/
  `RemoveApp` (respondem `Success` sem persistir).

### 7.5 Robustez / ideias

- `--doctor --json` para issue reports.
- Detectar mudança de contrato do site (ex.: versão diferente de `GetVersion`,
  comandos desconhecidos) e logar em vez de silenciar.
- Teste unitário do parser do XKB com fixtures de 2.42 e 2.47.
- Log com timestamp/rotação (hoje vai para o journal).

---

## 8. Limitações conhecidas (para o README/issue tracker)

- 3–4 slots no modo GNOME (5 no udev); `A6`+ não suportados.
- Um atalho por slot, global (não segue perfil/camada do teclado).
- Receptor 2.4 GHz não mapeado.
- Protocolo não documentado e sem autenticação; pode mudar sem aviso.
- Modo udev dá ao usuário leitura do teclado (igual `keyd`/`kanata`).
- Firefox não funciona (bloqueia `ws://` em página https).

---

## 9. Troubleshooting (cola)

```bash
systemctl --user status kassist
journalctl --user -u kassist -n 50
ss -ltnp | grep 40003
xdg-mime query default x-scheme-handler/launcherassist
gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings
gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kassist-f16/ binding
ls -l /dev/hidraw*; getfacl /dev/hidraw0
xdg-open 'launcherassist://x'          # testa o handler
python3 tests/smoke.py                 # protocolo (usa o serviço real)
```

Cuidado: **não** usar `pkill -f <padrão>` quando o padrão aparece na própria
linha de comando (isso já matou o shell durante os testes). Para encerrar uma
instância de teste use `fuser -k 40006/tcp`.

---

## 10. Mapa dos arquivos

```
kassist.py                          daemon + CLI (901 linhas, stdlib apenas)
install.sh                          instalador (--udev, --rules, --uninstall)
tests/smoke.py                      teste de protocolo (WS cru)
tools/selftest.py                   diagnóstico do ambiente + --simulate
tools/keypeek.py                    mostra keyval/keycode da tecla pressionada
docs/vm-testing.pt-BR.md            roteiro GNOME Boxes (Debian/Ubuntu)
docs/estado-atual.md                este documento
README.md                           visão geral, protocolo, matriz de distros
LICENSE                             MIT
packaging/kassist.service           unit systemd --user (template @EXEC@)
packaging/launcherassist.desktop    handler de URI (template @EXEC@)
packaging/99-keychron-assist.rules  regra udev (uaccess p/ evdev e hidraw)
```

---

## 11. Comandos que resumem o estado

```bash
# instalar/atualizar (sem root)
./install.sh
# permissão do site (WebHID), uma vez
sudo ./install.sh --rules
# 5 slots sem XKB (root, opcional)
./install.sh --udev

# verificação
./kassist.py doctor
python3 tools/selftest.py

# teste do protocolo sem navegador
python3 tests/smoke.py

# desfazer
./install.sh --uninstall
```
