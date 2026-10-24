# Testando em VM (GNOME Boxes) — Debian e Ubuntu

Objetivo: validar a instalação em Debian/Ubuntu sem tocar no sistema
principal. A VM cobre o fluxo inteiro, inclusive a tecla física, se você
redirecionar o teclado (seção 5).

---

## 1. Criar a VM

No Boxes: **Novo** → escolha a ISO.

| Distro | ISO recomendada | Observação |
|---|---|---|
| Ubuntu | 24.04.x LTS Desktop (amd64) | já vem com GNOME/Wayland e Firefox snap |
| Debian | 13 "trixie" GNOME (live ou netinst + task GNOME) | netinst: marque **GNOME** no tasksel |

- RAM 4 GB, disco 25 GB, firmware UEFI.
- Entre na sessão **GNOME (Wayland)** — padrão nas duas.
- **Não** redirecione o teclado ainda (seção 5).

---

## 2. Dependências dentro da VM

**Ubuntu:**

```bash
sudo apt update
sudo apt install -y python3 libglib2.0-bin xdg-utils desktop-file-utils zenity wtype
sudo snap install chromium
```

> A Launcher exige navegador Chromium/Chrome. **Firefox não serve** (bloqueia
> `ws://` a partir de página https).

**Debian:**

```bash
sudo apt update
sudo apt install -y python3 libglib2.0-bin xdg-utils desktop-file-utils zenity wtype chromium
```

Conferido nos repositórios: Debian 13 tem `wtype 0.4-3` e `chromium`; Ubuntu
24.04 tem `wtype` no universe e `chromium-browser` como wrapper do snap.

---

## 3. Levar o código para a VM

O Boxes usa rede user-mode: o host é o gateway da VM (normalmente `10.0.2.2`).

No **host**:

```bash
cd ~/development/keychron-assist-linux
tar czf /tmp/kassist.tar.gz .
cd /tmp && python3 -m http.server 8000
```

Na **VM**:

```bash
mkdir -p ~/kassist && cd ~/kassist
curl -fLo kassist.tar.gz http://10.0.2.2:8000/kassist.tar.gz
tar xzf kassist.tar.gz
```

Se `10.0.2.2` não responder, descubra o gateway com `ip route | grep default`
— o host é esse endereço.

---

## 4. Instalar

```bash
cd ~/kassist
./install.sh
```

Sem `sudo`. O instalador copia o binário, cria o `kassist.service` de usuário,
registra o handler `launcherassist://`, registra os atalhos do GNOME e termina
rodando o `doctor`.

### Permissão do WebHID (uma vez, com sudo)

O **daemon** não precisa de root, mas o **site** precisa de acesso a
`/dev/hidraw*` para gravar o keycode no teclado. No Fedora do host isso já
existia (regras do QMK/VIA). Numa VM limpa:

```bash
sudo ./install.sh --rules
```

Isso instala só `/etc/udev/rules.d/99-keychron-assist.rules` e recarrega o
udev — o backend continua sendo o GNOME. A saída deve mostrar
`ok: N de M nó(s) hidraw acessíveis`. Se o teclado ainda não estiver
redirecionado, o aviso é esperado e desaparece depois da seção 5.

### Slots nesta distro

Debian 13 e Ubuntu 24.04 vêm com `xkeyboard-config` 2.42, que **não define** o
`<FK19>`. O modo GNOME fica com **3 slots** (F16–F18) aqui, contra 4 no Fedora.
O instalador mostra `F19: sem keyval no XKB, pulando` — é esperado. Para os 5
slots nesta distro seria preciso `./install.sh --udev` (sudo). Use os slots 1–3.

---

## 5. Teste ponta a ponta (teclado redirecionado)

Boxes: **Preferências → Devices & Shares → USB Devices** → ligue o Keychron.

> O teclado passa a pertencer à VM; o host perde o teclado enquanto estiver
> redirecionado. O mouse continua no host, então dá para desligar pelo próprio
> Boxes. Se tiver um segundo teclado USB, use-o no host para não ficar sem
> entrada.

Na VM:

1. Abra o **Chromium** em <https://launcher.keychron.com>.
2. **Quick Start → Launch APP** → a lista de apps deve aparecer (100+).
3. Escolha um app (ex.: Calculadora), escolha um dos slots e salve.
4. Verifique a binding:

   ```bash
   python3 tools/selftest.py | grep bindings
   ```

5. **Pressione a tecla física.** O app deve abrir.

Se o site não conseguir salvar (ou não mostrar o teclado), confira a regra
hidraw da seção 4 e reconecte o teclado.

---

## 6. Teste sem teclado (simulação)

Serve para validar a cadeia do compositor quando não quiser redirecionar
hardware. Sem teclado o site não grava nada, então copie a binding do host:

```bash
# no host
cat ~/.config/keychron-assist-linux/binds.json
# na VM
mkdir -p ~/.config/keychron-assist-linux
# cole o JSON no arquivo binds.json
systemctl --user restart kassist
```

Os caminhos dos `.desktop` do GNOME batem entre Fedora e Debian/Ubuntu
(`/usr/share/applications/org.gnome.Calculator.desktop`). Confirme com
`test -f` antes.

Feche o app (senão o `gio launch` só ativa a janela existente) e simule:

```bash
python3 tools/selftest.py --simulate F16
```

O comando injeta o keyval real do slot (`XF86Launch7`) com `wtype` e verifica se
um processo novo apareceu:

```
-> simulando F16 (XF86Launch7) com wtype...
   OK: processo novo [12345] -> F16 abriu o app
```

---

## 7. O que cada teste cobre

| Etapa | `--simulate` | Tecla física |
|---|---|---|
| Site grava o keycode no teclado | não | sim (exige regra hidraw) |
| Firmware emite F16 (keycode 194) | não | sim |
| XKB traduz para `XF86Launch7` | sim | sim |
| Atalho do GNOME dispara | sim | sim |
| `kassist trigger` → `gio launch` | sim | sim |

---

## 8. Se algo falhar

```bash
python3 tools/selftest.py
systemctl --user status kassist
journalctl --user -u kassist -n 50
ss -ltnp | grep 40003
xdg-mime query default x-scheme-handler/launcherassist
ls -l /dev/hidraw*
```

- **A lista aparece, mas salvar a binding não grava nada**: falta a regra
  hidraw (seção 4). `sudo ./install.sh --rules` e reconecte o teclado.
- **Atalho não dispara logo após instalar**: relogue (o `gsd-media-keys`
  recarrega os grabs no login).
- **Lista de apps vazia**: `tools/selftest.py` mostra quantos apps foram
  encontrados; confira `XDG_DATA_DIRS`.
- **"Open" no site não abre nada**: teste `xdg-open 'launcherassist://x'` na VM.
  No Ubuntu com Chromium snap o pedido passa pelo portal; se falhar, anexe a
  saída de `journalctl --user -b | grep -i launcherassist`.
- **Porta ocupada**: `ss -ltnp | grep 40003` (pode ser instância antiga).

---

## 9. Coletar o resultado

```bash
python3 tools/selftest.py > ~/selftest.txt 2>&1
sed -n '1,30p' ~/selftest.txt
```

Cole a saída — com isso dá para fechar a matriz de distros.

---

## 10. Desfazer

```bash
./install.sh --uninstall    # binário, serviço, handler e atalhos
```

A regra udev (se instalada) e as bindings ficam; o desinstalador lembra o
comando para remover a regra.
