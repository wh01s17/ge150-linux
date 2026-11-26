<p align="center"><img src="docs/logo.svg" alt="GE150 Editor" width="560"></p>

# ge150-linux

Editor libre para Linux de la pedalera **Mooer GE150**: interfaz gráfica (`ge150`) y línea de comandos
(`ge150ctl`). El protocolo USB se obtuvo por ingeniería inversa del software oficial *Mooer Studio for GE150*
(Windows). El protocolo, la arquitectura y la hoja de ruta están en [`plan.md`](plan.md).

> **Estado:** verificado con una MOOER GE150 real, firmware V1.3.8 (ver
> [`docs/verificacion-hardware.md`](docs/verificacion-hardware.md)). Aun así, haz un backup antes de cambios grandes.
> Novedades en [`CHANGELOG.md`](CHANGELOG.md); cómo publicar una versión en [`PUBLISH.md`](PUBLISH.md).

<p align="center"><img src="docs/img/efectos.png" alt="Pestaña Efectos con el panel de IA" width="900"></p>

## Índice

- [Qué puede hacer](#qué-puede-hacer)
- [Instalación](#instalación)
  - [Con pipx (recomendada)](#con-pipx-recomendada)
  - [Con pip en un entorno virtual](#con-pip-en-un-entorno-virtual)
  - [Paquete de Arch Linux](#paquete-de-arch-linux)
  - [Acceso a la pedalera sin root](#acceso-a-la-pedalera-sin-root)
  - [Accesos directos](#accesos-directos-pipxpip)
  - [Para desarrollar](#para-desarrollar)
- [Actualizar](#actualizar)
- [Desinstalar](#desinstalar)
- [Interfaz gráfica](#interfaz-gráfica)
- [Línea de comandos](#línea-de-comandos)
- [Asistente de tono con IA](#asistente-de-tono-con-ia)
  - [Tus guitarras](#tus-guitarras)
  - [La pedalera como servidor MCP](#la-pedalera-como-servidor-mcp)
- [Cabinas de TONE3000](#cabinas-de-tone3000)
- [Desarrollo](#desarrollo)
- [Licencia](#licencia)

## Qué puede hacer

- Leer los 200 presets, el preset actual decodificado (modelos y parámetros), los IR de usuario y el
  estado del pedal de expresión.
- Editar en vivo: encender/apagar módulos, cambiar el modelo y mover parámetros.
- Guardar, renombrar y copiar presets, y ordenarlos arrastrándolos en la lista (con backup automático).
- Ver y cambiar los ajustes de sistema (INPUT LEVEL, SCREEN, CAB SIM THRU, OTG LEVEL) y la caja de ritmos.
- Asignar el pedal de expresión a un parámetro de cualquier efecto (o al volumen) desde la cadena de efectos.
- Importar y exportar presets `.mo`, con el formato de la app oficial.
- Cargar IR desde WAV a las ranuras de usuario (CAB 27-36), borrarlas y exportarlas a WAV.
- Backup completo y restauración en formato `.mbf`, el de la app oficial.
- Seguir los cambios hechos en la propia pedalera (footswitch, knobs).
- Asistente de tono con IA (Claude Code o Codex, con tu suscripción) y edición masiva de presets con la pestaña
  **Asistente**: escuchas cada propuesta en la pedalera antes de aplicarla y se hace un backup automático.
- Ficha de tus guitarras (la IA la completa y la usa para ajustar el tono a tu guitarra), historial de
  conversaciones con la IA y cabinas de TONE3000.
- Verificación automática con la pedalera real: `ge150ctl hw-check`.
- Accesos directos al instalar con pipx o pip: la app los crea en el primer inicio según tu escritorio.

## Instalación

Necesitas Linux y Python 3.11 o superior. Elige **una** de estas formas:

### Con pipx (recomendada)

[pipx](https://pipx.pypa.io/) instala la app en su propio entorno, sin tocar los paquetes del sistema. En Arch y
otras distribuciones actuales, `pip install` fuera de un entorno virtual está bloqueado; pipx lo resuelve.

```bash
sudo pacman -S python-pipx                 # Arch; en Debian/Ubuntu: sudo apt install pipx
pipx install 'ge150-linux[gui,ai]'         # ge150 (interfaz), ge150ctl (CLI) y ge150-mcp (IA)
pipx ensurepath                            # agrega ~/.local/bin al PATH (abre otra terminal después)
```

Al abrir `ge150` por primera vez te ofrece crear los accesos directos para tu escritorio (ver
[Accesos directos](#accesos-directos-pipxpip)).

Los extras son opcionales: `gui` instala la interfaz gráfica (PySide6) y `ai` el asistente IA (SDK de MCP). Con
`pipx install ge150-linux` queda solo la CLI. El catálogo de TONE3000 dentro de la app necesita además
`qt6-webengine` del sistema; sin él se abre en el navegador.

### Con pip en un entorno virtual

```bash
python3 -m venv ~/.local/share/ge150-venv
~/.local/share/ge150-venv/bin/pip install 'ge150-linux[gui,ai]'
~/.local/share/ge150-venv/bin/ge150        # o agrega esa carpeta bin al PATH
```

### Paquete de Arch Linux

Desde el repositorio clonado (instala también la regla udev, el acceso directo del menú y el ícono):

```bash
git clone https://github.com/wh01s17/ge150-linux.git
cd ge150-linux/packaging && makepkg -si
```

### Acceso a la pedalera sin root

Una sola vez, y solo si no usaste el paquete de Arch (que ya la instala). Después, desconecta y vuelve a conectar
la pedalera:

```bash
curl -fsSL https://raw.githubusercontent.com/wh01s17/ge150-linux/main/udev/70-mooer-ge150.rules \
  | sudo tee /etc/udev/rules.d/70-mooer-ge150.rules
sudo udevadm control --reload && sudo udevadm trigger
```

Para comprobar que la ve: `ge150ctl probe` (debe mostrar `MOOER_GE150` y el firmware).

### Accesos directos (pipx/pip)

pipx y pip no crean accesos directos, así que la primera vez que abres `ge150` la app pregunta si quieres
agregarlos, según tu escritorio:

- **Siempre**, la entrada del menú de aplicaciones (`~/.local/share/applications/ge150.desktop`) con su ícono. La
  leen los menús de GNOME, KDE Plasma, Xfce, Cinnamon, MATE, LXQt o Budgie y los lanzadores de los gestores de
  ventanas en mosaico (rofi, wofi, fuzzel… en Hyprland, Sway o i3).
- **Además, un ícono en el escritorio** en los entornos que los muestran (KDE Plasma, Xfce, Cinnamon, MATE, LXQt,
  LXDE, Budgie, Deepin), marcado como confiable para que abra sin advertencias. Nunca si tu carpeta Escritorio es
  tu carpeta personal.

Si dices que no, se crean o se quitan cuando quieras desde **Archivo → Crear accesos directos** (o *Quitar
accesos directos*). El paquete de Arch ya instala su propio acceso en el menú y la app no lo duplica.

### Para desarrollar

```bash
git clone https://github.com/wh01s17/ge150-linux.git && cd ge150-linux
python3 -m venv --system-site-packages .venv   # reutiliza PySide6 y pyusb del sistema
.venv/bin/pip install -e '.[gui,ai,dev]'       # PySide6 solo se instala si el sistema no lo tiene
```

## Actualizar

| Instalación | Comando |
|---|---|
| pipx | `pipx upgrade ge150-linux` |
| pip en entorno virtual | `~/.local/share/ge150-venv/bin/pip install -U 'ge150-linux[gui,ai]'` |
| Paquete de Arch | `git pull` en el repositorio clonado y otra vez `cd packaging && makepkg -si` |
| Desarrollo | `git pull` (con `pip install -e` los cambios del código se usan al instante) |

Las novedades de cada versión están en [`CHANGELOG.md`](CHANGELOG.md). Tus presets viven en la pedalera y tus
datos en tu carpeta personal: actualizar no los toca.

## Desinstalar

1. Quitar la app, según cómo la instalaste:

   ```bash
   pipx uninstall ge150-linux                 # pipx
   rm -rf ~/.local/share/ge150-venv           # pip en entorno virtual
   sudo pacman -Rns ge150-linux-git           # paquete de Arch (quita también la regla udev y el menú)
   ```

   Antes de `pipx uninstall`, quita los accesos directos desde **Archivo → Quitar accesos directos** (o bórralos
   a mano, ver el paso 4).

2. Si la registraste para Claude Code o Codex (`ge150ctl mcp-install`):

   ```bash
   claude mcp remove --scope user ge150
   codex mcp remove ge150
   ```

3. Si instalaste a mano la regla udev:

   ```bash
   sudo rm /etc/udev/rules.d/70-mooer-ge150.rules && sudo udevadm control --reload
   ```

4. Si quedaron accesos directos (el del escritorio solo existe en los entornos que muestran íconos ahí):

   ```bash
   rm -f ~/.local/share/applications/ge150.desktop ~/.local/share/icons/hicolor/scalable/apps/ge150.svg
   rm -f "$(xdg-user-dir DESKTOP)/ge150.desktop"
   ```

5. Tus datos se conservan por si vuelves a instalar. Para borrarlos también (**incluye los backups automáticos de
   la pedalera, las conversaciones con la IA y tus guitarras**):

   ```bash
   rm -rf ~/.config/ge150 ~/.local/share/ge150 ~/.cache/ge150
   ```

## Interfaz gráfica

```bash
ge150            # con la pedalera conectada
ge150 --sim      # con el simulador, sin hardware
```

- En pantallas grandes (4K, 1440p) la interfaz se agranda sola si el sistema no la escala; para fijar otro tamaño:
  *Ver → Tamaño de la interfaz* (se aplica al reiniciar). La variable `QT_SCALE_FACTOR`, si la defines, tiene
  prioridad.

- Abajo, siempre visible, la **cadena de efectos** (de IN a OUT): cada módulo con la imagen de su equipo, el
  modelo y su LED de encendido. Un clic abre su página.
- Un clic en la lista de la izquierda carga el preset (si hay cambios sin guardar, pregunta antes). Para cambiar
  el orden, **arrastra** un preset a su nuevo lugar: los de entre medio se corren un puesto. Pide confirmación,
  hace antes un backup automático completo y muestra el avance mientras reescribe la pedalera.
- La página del módulo muestra el equipo original en grande (foto o dibujo), una galería con todos los
  modelos para elegir y perillas: se giran arrastrando, con la rueda o con las flechas, y con doble clic se
  escribe el valor.
- En las cabinas, el micrófono se arrastra frente al parlante para cambiar CENTER y DISTANCE.
- **Pedal de expresión**: su tarjeta EXP está al final de la cadena, aparte (no procesa la señal), y el efecto que
  mueve lleva la etiqueta EXP. En su página eliges qué controla: el **volumen** (con mínimo y máximo), **un
  efecto** (cualquiera de los encendidos, o un modelo nuevo, y el parámetro) o **nada**, con un atajo para wah. A
  la izquierda, el pedal visto de costado se inclina según su posición en vivo. Lo que hay que saber de la GE150:

  - una asignación nueva **se activa al guardar el preset** (la página lo recuerda con un botón Guardar);
  - mientras el pedal mueve un parámetro, ese parámetro sigue al pedal: al quitarle el control desde la página,
    vuelve al valor que tenía antes;
  - la pedalera solo informa la posición del pedal a través del parámetro que mueve: en volumen no se ve;
  - el interruptor de punta del pedal activa y desactiva el control del parámetro, y la app lo sigue;
  - la calibración se hace en la propia pedalera (menú EXP → CALIBRATE): no se puede iniciar ni leer por USB.

<p align="center"><img src="docs/img/exp.png" alt="Página del pedal de expresión moviendo el POSITION de un CRY WAH" width="900"></p>

- Cada equipo se dibuja a partir del original (marca, colores, rejilla, perillas). Si prefieres una foto propia,
  pon `<id>.jpg`, `.png` o `.webp` en `~/.local/share/ge150/photos/` (el `id` de cada equipo está en
  `src/ge150/data/gear.json`) y se muestra en lugar del dibujo.
- Pestaña **TONE3000**: el catálogo de IR de cabinas de [TONE3000](https://www.tone3000.com) dentro de la app. Al
  elegir una IR aparece su ficha (imagen, autor, licencia, descripción y archivos) con la ranura de destino y
  **Agregar a la pedalera**. Necesita `qt6-webengine`; sin él, el catálogo se abre en el navegador.
- Botón **✦ IA** (Ctrl+K): panel lateral para pedir sonidos a Claude o Codex (ver más abajo).
- **Ayuda → Acerca de**: versión, autor, licencia, información de tu sistema (para reportar problemas), dónde se
  guardan tus datos y créditos.

## Línea de comandos

```bash
ge150ctl probe                         # nombre y versión del firmware
ge150ctl presets                       # los 200 nombres
ge150ctl current                       # preset actual decodificado
ge150ctl snapshot --out ge150.json     # copia de todo lo legible: hazla antes de escribir
ge150ctl models delay                  # modelos de un módulo (sin pedalera)

ge150ctl set reverb level 40           # en vivo (no se guarda)
ge150ctl off mod / on mod
ge150ctl type amp "65 US TW"           # o por número: type amp 2
ge150ctl select 12

ge150ctl system                        # INPUT LEVEL, SCREEN, CAB SIM THRU, OTG LEVEL
ge150ctl system-set input 0.5          # input|otg: dB (- = silencio); screen 0-20; cab-left|cab-right on/thru
ge150ctl rhythm                        # caja de ritmos: estado, modo, estilo, BPM, volumen
ge150ctl rhythm-set bpm 120            # state on/off, mode rhythm/metronome, style N, bpm, vol

ge150ctl save [--slot N] [--name X]    # persistentes: piden confirmación (o -y)
ge150ctl rename 12 "Mi Preset"
ge150ctl copy 12 150
ge150ctl export 12 mi.mo | export all carpeta/
ge150ctl import mi.mo 150
ge150ctl ir-export carpeta/            # IR de usuario → WAV
ge150ctl ir-upload 8 cab.wav           # WAV → ranura IR 8 (CAB 34)
ge150ctl ir-delete 8
ge150ctl nr-upload 1 captura.amp       # experimental: la GE150 procesa NR (AMP 56-65), pero no hay .amp para ella
ge150ctl backup GE150.mbf              # backup completo (solo lectura)
ge150ctl restore GE150.mbf             # SOBRESCRIBE toda la pedalera
ge150ctl factory-reset --backup antes.mbf  # BORRA TODO: valores de fábrica (pide «BORRAR TODO»)

ge150ctl ai "tono de Slash en Sweet Child O' Mine"   # asistente IA: aplica el tono en vivo (sin guardar)
ge150ctl ai --dry-run --provider codex "clean con chorus de los 80"

ge150ctl hw-check --slots 198,199,200  # verifica en la pedalera real (editor cerrado); deja todo como estaba

ge150ctl --sim <comando>               # cualquier comando contra el simulador
ge150ctl --raw <comando>               # muestra cada reporte USB de 64 bytes
```

## Asistente de tono con IA

`ge150ctl ai` le pide el tono a **Claude Code** (`claude`) o a **Codex** (`codex`), las herramientas de línea de
comandos de las suscripciones de Claude y ChatGPT: no hace falta clave de API, usa la cuota de tu plan. Recibe el
catálogo exacto de la GE150 (modelos, en qué equipo se basa cada uno y rangos) y tu preset actual, propone el patch
completo y la app lo aplica en vivo; guárdalo con `ge150ctl save` si te gusta. Las herramientas se ejecutan sin
acceso a archivos ni otras herramientas.

En la interfaz gráfica, el panel **✦ IA** (Ctrl+K) conversa con Claude o Codex, que cambian el sonido con las
herramientas MCP de abajo: ves cada paso en el chat y en la pedalera, puedes seguir pidiendo ajustes ("un poco menos
de ganancia"), **Deshacer** vuelve al sonido anterior y **Detener** corta la petición. La IA solo guarda un preset o
carga una IR de TONE3000 si se lo pides, y siempre te pregunta antes en una ventana.

La pestaña **Asistente** sirve para cambiar varios presets a la vez ("baja el reverb a 20 en todos los Av83"): marcas
presets en la lista, se lo pides a la IA o usas la edición manual (módulo, estado, modelo, un parámetro fijado o
sumado), y los cambios quedan en **Cambios pendientes** (antes → después por módulo) hasta que aprietas **Aplicar**.
**Deshacer última edición** vuelve a escribir los presets como estaban. El preset cargado con cambios sin guardar no
se toca. Antes de escribir se hace solo un **backup completo** (`.mbf`, el mismo de *Archivo → Backup completo*) en
`~/.local/share/ge150/backups/` (se guardan los últimos 20; *Archivo → Abrir carpeta de backups automáticos*); si el
backup falla, no se escribe nada.

<p align="center"><img src="docs/img/asistente.png" alt="Pestaña Asistente con cambios pendientes" width="900"></p>

Los dos chats guardan sus conversaciones: el botón del reloj abre el historial y, al abrir una, la IA retoma la
conversación y la pedalera carga el preset de esa conversación.

### Tus guitarras

En *Herramientas → Mis guitarras…* registras las guitarras que tienes: escribes marca, modelo y número de serie e
**Identificar con IA** completa la ficha de fábrica (año, país, maderas, mástil, escala, pastillas, controles,
puente, cuerdas) y busca su foto. Cada dato indica qué tan seguro es (**oficial**, **verificado**, **probable** o
**deducido**); revisa los datos y corrígelos antes de guardar.

Con **Usar esta guitarra** (o el selector de guitarra en el chat) la IA sabe con qué estás tocando: no es lo mismo
pedir "un crunch bluesero" con una semihueca de humbuckers que con una Strat de single coils, así que ajusta la
ganancia, la ecualización y el resto del preset a tus pastillas y tu guitarra.

<p align="center"><img src="docs/img/guitarras.png" alt="Ventana Mis guitarras con una ficha identificada por la IA" width="900"></p>

### La pedalera como servidor MCP

`ge150-mcp` expone la pedalera a cualquier cliente MCP (Claude Code, Codex…) con herramientas para leer el catálogo,
el preset actual, los presets guardados, las IR de usuario y los ajustes, y para cambiar el sonido en vivo
(`set_module`, `apply_patch`, `undo`, `select_preset`). Con TONE3000 también busca y descarga IR de cabinas
(`tone3000_search`, `tone3000_get`, `load_tone3000_ir`).

```bash
.venv/bin/pip install -e '.[ai]'       # SDK de MCP
ge150ctl mcp-install                   # registra el servidor en Claude Code y/o Codex
claude "ponme el tono de Gilmour en el solo de Comfortably Numb"
codex "un clean funk tipo Nile Rodgers"
```

Con el editor gráfico abierto, `ge150-mcp` se conecta a él (solo un programa puede usar la pedalera): ves cada
cambio de la IA al instante y nada se escribe en la memoria de la pedalera (`save_preset`, `load_tone3000_ir`) sin
que lo confirmes en una ventana del editor. Sin el editor abierto, `ge150-mcp` abre la pedalera directamente y
esas dos herramientas se rechazan. La IA nunca restaura, resetea ni actualiza el firmware.

## Cabinas de TONE3000

Descarga IR de cabinas de [TONE3000](https://www.tone3000.com) y cárgalas en las ranuras de usuario (CAB 27-36),
desde la pestaña TONE3000 de la app, desde la línea de comandos o pidiéndoselo a la IA.
Una vez: copia la clave publicable (*Publishable Key*, `t3k_pub_…`) de los ajustes de API de tu cuenta de TONE3000.
No hace falta registrar redirects: TONE3000 siempre acepta `http://127.0.0.1`. La *Secret Key* no se usa; no la
compartas.

<p align="center"><img src="docs/img/tone3000.png" alt="Pestaña TONE3000 con el catálogo y la ficha de una IR" width="900"></p>

```bash
ge150ctl t3k-key t3k_pub_…               # guarda tu clave publicable
ge150ctl t3k-login                    # inicia sesión en el navegador
ge150ctl t3k-load 4                   # elige una IR en el catálogo de TONE3000 y la carga en la ranura 4
ge150ctl t3k-load 4 --tone 12345      # o directo por id de tono
ge150ctl t3k-status                   # sesión e IR descargadas (autor y licencia)
```

Al elegir un CAB de usuario en la pestaña Efectos se ve de qué IR de TONE3000 viene. Se muestran autor y licencia
de cada IR; respétala (por ejemplo, `cc-by-nc` = solo uso no comercial). Las IR
descargadas quedan en `~/.cache/ge150/tone3000/` y no se versionan.

## Desarrollo

```bash
.venv/bin/python -m pytest              # incluye tests de la GUI en modo offscreen
.venv/bin/ruff check src tests tools
.venv/bin/mypy
python3 -I tools/build_catalog.py       # regenera src/ge150/data/effects.json desde reference/
```

La interfaz usa el español por defecto. Para añadir otro idioma, crea un catálogo JSON
en `src/ge150/data/translations/` con el código del idioma como nombre de archivo,
por ejemplo `fr.json`. Las claves son los textos originales marcados con `tr()`.
Mantén los nombres de los parámetros como `{slot}` en los mensajes traducidos.
Las entradas que falten se muestran en español. Prueba el catálogo con
`ge150 --sim --language fr`.

Regla del proyecto: ningún archivo de código supera 1000 líneas (ver `plan.md` §0).
El material del fabricante (`reference/`) no se versiona.

## Licencia

[MIT](LICENSE). El software oficial de Mooer y su material (`reference/`) no forman parte del proyecto ni se
distribuyen aquí.
