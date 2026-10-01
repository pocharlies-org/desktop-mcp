# ARCHITECTURE — desktop-mcp

Servidor MCP de control de escritorio: capturas, ratón, teclado, aplicaciones, AppleScript/JXA y el árbol de accesibilidad de macOS. Un solo conjunto de herramientas con dos backends, macOS y Linux/X11. Se distribuye como plugin de Claude Code (`desktop-control`) o como servidor MCP suelto.

## Clientes y versiones

- Claude Code (plugin `desktop-control@pocharlies-plugins`, marketplace en `.claude-plugin/marketplace.json`) y opencode (registrado por `install.sh`).
- Plataformas: macOS (con `cliclick` y Xcode CLT) y Linux/X11 (`xdotool`, `imagemagick`, `wmctrl`, `xrandr`; sin Wayland).
- La versión es la constante `VERSION` de `server.py` (valor no verificado).

## Dependencias (en ambos sentidos)

- Depende de: Python 3.9+ (solo biblioteca estándar), `cliclick`, Xcode CLT, los permisos de macOS Screen Recording y Accessibility (concedidos al proceso que ejecuta el servidor) y, en Linux, las herramientas X11.
- Dependen de él: las sesiones de Claude/opencode que lo registran (herramientas `mcp__desktop__*` o `mcp__plugin_desktop-control_desktop__*`).
- Incorpora un proxy de Peekaboo (`peekaboo_proxy`, #1/#2): las herramientas MCP de Peekaboo más AppleScript y el aviso en pantalla; el fork de Peekaboo se mantiene aparte (`peekaboo-fork-update` en `x86-host-runtime-pocharlies`).

## Stack

Python estándar (`plugins/desktop-control/server.py`, JSON-RPC propio sobre stdio), C y Objective-C para los helpers de macOS (`macos/helper.c`, `launcher.c`, `overlay.m`), shell (`install.sh`, `bootstrap.sh`, `macos/boot.sh`). Ningún paquete de terceros.

## Componentes compartidos

- `plugins/desktop-control/server.py`: tabla `TOOLS` única para los dos backends; añadir una herramienta es una entrada ahí y su función por backend.
- `plugins/desktop-control/macos/overlay.m`: el aviso azul pulsante y el banner con el nombre de la sesión que controla el escritorio; no se omite nunca.
- `plugins/desktop-control/bootstrap.sh`: compila los helpers de macOS en la primera ejecución.

## Cómo se construye

Se mantiene la biblioteca estándar: no se añaden dependencias de Python. Un cambio de herramienta se hace en `server.py` y se prueba en las dos plataformas o se declara cuál no se probó. `install.sh` (flags `--claude`, `--opencode`, `--no-register`, `--daemon`, `--uninstall`) es la única vía de instalación.

## Tests

`install.sh` ejecuta una autoprueba al terminar. No hay suite de tests ni CI en el repo: es un hueco.

## CI/CD y despliegue

Sin CI. Se publica con el propio repo (`/plugin marketplace add pocharlies-org/desktop-mcp`). Con `--daemon` se instala `DesktopMCP.app`, que hereda los permisos de macOS.

## Decisiones y trampas

- El daemon supervisa los helpers que necesitan los permisos concedidos a la app (#2) y limpia el aviso al recibir SIGTERM (#3); el aviso se coloca en cada pantalla a su escala (#4).
- Los permisos de macOS se conceden al proceso que ejecuta, no al repo: pasar de terminal a `--daemon` obliga a repetir la concesión.
- Sin CI ni tests automáticos: toda regresión se ve en el uso.
