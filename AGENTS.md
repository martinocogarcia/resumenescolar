# Resumen Escolar - Codex Instructions

## Lección operativa OCI

En la VM, las pruebas y el cron deben ejecutar `./.venv/bin/python` desde
`/opt/resumen-escolar`; el `python3` del sistema puede no tener Playwright,
aunque las dependencias estén instaladas en el entorno virtual del despliegue.

Use Spanish by default with Martin. This project is a local Python app that uses Playwright/Chrome to read visible evidence from SchoolNet and Google Classroom, then generates a ChatGPT prompt for a school infographic about Gabito Garcia.

## Global orchestration

The general Codex configuration/orchestration project is `C:\Users\Martin\Documents\Codex\2026-05-25\ayudame-a-configurar-codex-en-base`. General rules established there apply here too, especially execution from Codex first, one-line PowerShell fallback with disposable logs, secret handling, and cross-project coordination. If Martin updates global behavior there, mirror any relevant durable rule into this local `AGENTS.md`.

## Shared operational access map

This map applies across Martin projects, even when this repo does not directly use a given VM. Do not print private key contents, tokens, passwords, DuckDNS tokens, API keys, OCIDs, or values from local parameter files.

- GitHub owner/namespace: `martinocogarcia`; preferred remotes use `git@github.com:martinocogarcia/<repo>.git`.
- Shared OCI VM for `worldcup2026` and `mercadopublico-reportes`: SSH target `ubuntu@40.233.127.100`. WorldCup remote dir `~/worldcup2026`, URL `https://polla2026.duckdns.org`, health `https://polla2026.duckdns.org/healthz`, local VM health `http://127.0.0.1:8106/healthz`. Mercado Publico remote dir `/home/ubuntu/mercadopublico-reportes`, URL `https://mpreportes.duckdns.org/reportes`, local/admin listener `http://127.0.0.1:8080`. Shared nginx container: `mp-reportes-nginx`. Credential references: `$HOME/.ssh/id_rsa`, `.oci-deploy\deploy-params.local.ps1`, `.oci-deploy\worldcup2026_ssh`; never print contents.
- OCI VM for `oracle-form-app`: SSH target `ubuntu@129.153.51.108`, public URL `http://129.153.51.108/`, health `http://129.153.51.108/health`, remote app dir `/opt/oracle-form-app`, persistent dir `/opt/oracle-form-persistent`. Current Windows SSH/deploy key path: `C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511`; SSH command: `ssh -i "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511" ubuntu@129.153.51.108`; older key that previously worked: `C:\Users\Martin\Downloads\ssh-key-2026-03-18.key`; Cloud Shell fallback may still use `$HOME/.ssh/oci_original.key`. Never print key contents.
- Known Windows SSH key paths: shared VM `C:\Users\Martin\.ssh\id_rsa`; legacy/full WorldCup OCI key `C:\Users\Martin\Documents\New project 3\.oci-deploy\worldcup2026_ssh`; oracle-form VM `C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511`; older oracle-form key `C:\Users\Martin\Downloads\ssh-key-2026-03-18.key`. Do not ask Martin for `RUTA_A_TU_LLAVE`; use these paths when building SSH commands or one-line PowerShell fallbacks. Never print key contents.

## Corporate laptop execution rule

- First priority: execute work from inside Codex when the available tools and permissions allow it.
- If PowerShell, deploy scripts, installs, browser automation, or local commands are blocked by corporate policy, sandbox limits, missing permissions, or security tooling, do not keep retrying.
- If `scripts\start_vm_login_session.ps1` fails with `ssh: connect to host 129.153.51.108 port 22: Connection timed out` while `http://129.153.51.108/health` still returns OK, treat the Resumen Escolar login session as blocked by SSH ingress. Do not retry the noVNC login tunnel until OCI networking/host firewall access to TCP 22 is restored.
- When the laptop is connected to the corporate VPN and SSH is limited to a home/public `/32`, disconnect the VPN and retry once before changing OCI ingress again. The VPN may use a different egress IP or filter TCP 22; reconnect it once the narrow test completes.
- External OCI CLI diagnostics that loop over profiles or regions must pass explicit `--connection-timeout`, `--read-timeout`, and usually `--no-retry` to each `oci` call; otherwise a corporate network or regional endpoint stall can leave the helper stuck without progress.
- For external OCI CLI helpers that need the API key passphrase, prompt with `Read-Host -AsSecureString` and set process-scoped `OCI_CLI_KEY_PASSPHRASE`, `OCI_CLI_PASSPHRASE`, and `OCI_CLI_PASS_PHRASE` for compatibility. Do not print or persist the passphrase.
- Instead, give Martin exactly one PowerShell line to run outside Codex.
- That line must write stdout and stderr to a disposable log file in a clear path, preferably under this project or `%TEMP%`, for example with `*> "path\codex-task-log.txt"`.
- Tell Martin to reply `listo` after running it; when he does, read that log file from disk and continue from the result.
- Do not assume the `codex` CLI is available in Martin's external PowerShell. If `codex debug prompt-input` or similar diagnostics are needed, try them inside Codex first; if blocked or unavailable externally, verify context by reading `AGENTS.md` files directly.
- When giving Martin an external PowerShell one-liner, output the command exactly once in its own isolated `powershell` code block and do not repeat the same command in prose. If copy/paste from Codex duplicates the line as `scriptscript`, tell him not to run it, paste into a plain text editor first, keep only one copy of the line, then run it. For long or fragile commands, prefer creating a disposable `.ps1` script in the workspace and give one short PowerShell line that runs it and writes the log.
- Every external PowerShell one-liner given to Martin must end with a Windows completion sound, even if the command fails. For long commands, deploys, SSH, OCI, remote tests, or log-writing scripts, prefer a one-line `try { <work> } finally { [System.Media.SystemSounds]::Asterisk.Play() }` pattern so the sound runs on failure too. Keep the prior rules: exactly one isolated PowerShell line, stdout/stderr to a disposable log when appropriate, and ask Martin to reply `listo`.
- External PowerShell one-liners that may take noticeable time must print concise progress markers to the console, such as `[1/5] Preparing`, `[2/5] Running`, or `[3/5] Checking results`. The console should show only stage summaries and whether the command is advancing; verbose output belongs in the disposable log. When logging is required, do not redirect the entire wrapper in a way that hides progress markers. Print progress to the console and redirect noisy work commands or script internals to the log.
- For deploys, Playwright scans, copies, and other long operations, use granular stages (normally 8-15) with start, finish, duration, and a verifiable result. Report connection, upload, remote preparation, dependency verification, execution, and validation separately; set explicit timeouts for external package/network operations.
- When Martin runs an external PowerShell one-liner and the resulting log or pasted output reveals an error that could recur, treat it as operational learning. Before finishing, update the relevant `.md` context files, including the global `C:\Users\Martin\.codex\AGENTS.md` and any affected local project `AGENTS.md`, with the corrected pattern or avoidance rule. This applies to recurring PowerShell formatting, quoting, redirection, PATH, sandbox/corporate-policy, SSH, OCI, logging, progress-marker, and completion-sound mistakes. Do not persist secrets while documenting the lesson.

## SOC-friendly network validation

- Avoid ad hoc PowerShell `Invoke-WebRequest` or `Invoke-RestMethod` from the corporate laptop to DuckDNS or other dynamic DNS domains unless Martin explicitly asks for that exact method or no safer option is available.
- For public app checks, prefer documented health endpoints, the in-app Browser tool, or remote/Cloud Shell validation.
- If a Windows local HTTP check is necessary, prefer `curl.exe` with a narrow exact FQDN/path over PowerShell web cmdlets.
- If Falcon, SOC, or another security tool blocks a network command, stop retrying that pattern and summarize the safer next option.
- If an IP-check site is blocked by the Oracle Acceptable Use Policy or Global Domain BlockList, do not try alternate public IP services. Use OCI Console's "My IP"/source helper in the SSH ingress editor; do not persist the detected address in documentation.

## Safety

- Do not print or persist secrets, browser cookies, profiles, credentials, tokens, `.env`, `.oci`, `*.pem`, or personal data beyond what is necessary for the task.
- Prefer README, `.gitignore`, scripts, generated non-sensitive logs, and source code for context.
- Do not delete or overwrite `outbox`, `.runtime`, diagnostic bundles, or browser/profile data unless Martin explicitly asks for cleanup and a backup/quarantine plan is in place.

## Project-specific constraints

- Estado actual (2026-09): la app local genera el prompt/infografia con evidencia visible de SchoolNet, Google Classroom y Calendario SSCC de 4A. Incluye recuperacion controlada de sesion y ejecucion diaria, pero SSO/MFA permanece manual.
- Puede extraer materiales recientes de Classroom relacionados con evaluaciones futuras y guardarlos como salida local; no publicar perfiles, cookies, adjuntos escolares ni evidencia personal en Git.
- El navegador compartido de larga vida debe desacoplarse al finalizar; no cerrar el contexto remoto reutilizable ni borrar locks de perfil a ciegas.

- Local code folder: `C:\Users\Martin\Documents\Codex\resumen_escolar`.
- GitHub remote: `git@github.com:martinocogarcia/resumenescolar.git`.
- No OCI hosting for this app; it is a local browser-assisted workflow.
- Local app default: `http://127.0.0.1:8765/`.
- Main entrypoint: `resumen_escolar/app.py`.
- Install/run helpers: `install_deps.cmd`, `run_resumen_escolar.cmd`.
- Runtime/output folders: `.runtime\`, `outbox\`, `diagnostic_bundle_*`. Treat these as local state; do not delete without explicit cleanup instructions.
- Do not switch to Gemini, OAuth, or Google Classroom API unless Martin explicitly changes the requirement.
- Prefer Playwright/DOM-visible extraction from the browser.
- The course context observed for Classroom is 4-A and the classwork URL pattern under `https://classroom.google.com/w/ODQ5Nzk2MDk0NDk5/t/all`.
- SchoolNet URL in code: `https://schoolnet.colegium.com/webapp/es_CL/login`.
- Before changing extraction logic, read `README.md` and inspect the relevant functions in `resumen_escolar/app.py`.
Toda instrucción externa de PowerShell debe escribir stdout y stderr en un
archivo de log desechable para lectura posterior desde Codex; no basta con
mostrar la salida solamente en consola.
Si Playwright informa `Failed to create a ProcessSingleton` o `SingletonLock`
en el perfil persistente de Chromium, no borrar locks ni matar procesos a
ciegas: el perfil está siendo usado por otra instancia. Cerrar primero la
sesión Chromium/noVNC de forma normal o ejecutar la prueba cuando el perfil
esté libre.
Si Playwright informa `Failed to create a ProcessSingleton` o `SingletonLock`
en el perfil persistente de Chromium, no borrar locks ni matar procesos a
ciegas: el perfil está siendo usado por otra instancia. Cerrar primero la
sesión Chromium/noVNC de forma normal o ejecutar la prueba cuando el perfil
esté libre.
Cuando Chromium persistente se ejecuta fuera de una sesión X y falla con
`Missing X server or $DISPLAY`, no insistir en relanzarlo headful por SSH.
Usar `--headless=new` con el mismo perfil y CDP para conservar cookies sin
depender de una pantalla gráfica.
El iniciador noVNC de login debe elegir una pantalla X virtual libre (por
ejemplo entre `:99` y `:109`) en vez de asumir `:99`; no borrar locks ni
terminar una pantalla X existente de otro proceso solo para reutilizarla.
El Chromium de la sesión noVNC debe iniciarse con CDP en
`127.0.0.1:9222`; el cron se conecta a ese puerto para reutilizar el perfil
con login y, sin él, falla con `connect ECONNREFUSED 127.0.0.1:9222`.
Los runners remotos que ejecutan Python directamente deben cargar primero
`/opt/resumen-escolar/config/automation.env`; de lo contrario no reciben el
OCID del secreto SchoolNet ni la URL CDP y pueden reportar `needs_login` o
intentar el Chromium empaquetado por defecto.
Después de una instrucción PowerShell externa, aceptar `listo`, `ok` o `k`
como confirmaciones equivalentes.

## Uso de OCI Safe Deploy

Cuando Martin solicite un deploy, redeploy, validacion post-deploy o diagnostico de un deploy fallido en OCI, indicar al inicio que se aplicara `$oci-safe-deploy` y seguir sus instrucciones. Si la skill no estuviera disponible, decirlo y aplicar las reglas de deploy de este archivo.

## Lecciones de diagnostico SchoolNet

- Un estado `SchoolNet - Calificaciones=needs_login` con la nota `No pude construir una lectura estructurada de la tabla de calificaciones P1/P2` no demuestra que la sesion haya expirado: puede ser una pantalla autenticada cuyo DOM/tabla cambio o aun no termino de cargar. Recoger evidencia tecnica antes de pedir login manual y clasificarlo como error de extraccion, no como login.
- Al enviar un comando remoto por SSH desde una cadena PowerShell, no incrustar `python -c` con comillas anidadas para interpretar JSON: el shell remoto puede perder esas comillas (por ejemplo `NameError: Browser`). Para comprobaciones simples de CDP usar `curl ... | grep` o un archivo de script remoto separado.
- Cuando el diagnostico remoto requiera pipes, `grep` o regexes, codificar el script Bash completo en Base64 y ejecutarlo como una unica carga remota; no pasar pipelines con comillas crudas como argumento SSH desde PowerShell.
- Al analizar la salida de una invocacion nativa de PowerShell que puede ser un arreglo, no depender de `$Matches` despues de usar `-match` sobre el arreglo: puede quedar nulo. Unir la salida y usar `[regex]::Match(...).Groups[1].Value` antes de convertir el codigo de estado.
- No mantener una sesion SSH interactiva durante una extraccion Playwright de varios minutos: puede terminar con `client_loop: send disconnect: Connection reset` antes de devolver el resultado. Iniciar la prueba remota con `nohup`, registrar su salida en la VM y consultar estado/log con conexiones SSH cortas; no interpretar ese reset como un fallo de SchoolNet.

## OCI CLI Windows file reference

When passing complex JSON files to OCI CLI on Windows, use `file://C:\path\file.json`.
Do not use `file:///C:/path/file.json` with OCI CLI 3.81.1; it can resolve that as
`/C:/path/file.json` and fail before sending the request to OCI.

## Python unittest paths on Windows

When a unittest file has an absolute Windows path containing spaces, run the test file
directly with `python <absolute-test-file> -v`. Avoid passing that path to
`python -m unittest`; unittest can reinterpret it as a dotted module and fail with
`ValueError: Empty module name`.

## SSH ingress timeout skill recommendation

When an OCI SSH connection times out and evidence shows that the instance is RUNNING
or its HTTP health endpoint still responds, and TCP 22 ingress is restricted to a stale
client /32, explicitly recommend running $oci-update-client-ip as the remediation.
Use its dry-run before apply. Do not recommend this skill for Permission denied, a bad
SSH key, DNS failure, a stopped instance, host firewall issues, or sshd failure.

## Oracle APEX como opcion preferente de interfaz y hosteo corporativo

Al diseñar o migrar aplicaciones corporativas Oracle orientadas principalmente a consulta, formularios, reportes y tablas, evaluar Oracle APEX como la opcion preferente para alojar la interfaz. Esta preferencia no convierte APEX en una solucion obligatoria ni implica trasladar toda la aplicacion: validar primero compatibilidad funcional, clasificacion de datos, autenticacion, conectividad, limites del workspace y operacion.

- Preferir una arquitectura hibrida cuando ya exista logica Python, procesos programados, integraciones externas o datos persistentes en OCI: mantener esas funciones en el backend y exponer a APEX solamente APIs REST JSON estrechas, documentadas y de solo el alcance necesario.
- Definir un contrato JSON estable antes de configurar APEX. Si la respuesta usa un envoltorio como `{"items": [...]}`, configurar `items` como Row Selector del REST Data Source. Mantener alineados los nombres y tipos del Data Profile con el JSON real; si cambia el endpoint, volver a descubrir o corregir el perfil antes de modificar regiones a ciegas.
- Mantener en la fuente los campos tecnicos necesarios para la presentacion, aunque se oculten al usuario. Por ejemplo, una columna `DETAILS_URL` puede ser el destino de un enlace declarativo y `IS_NEW` puede alimentar reglas visuales sin mostrarse en el reporte.
- Configurar enlaces, orden, etiquetas, visibilidad y formatos mediante Page Designer siempre que sea posible. Para codigos navegables, usar una columna Link con Target Type `URL`, destino `#DETAILS_URL#` y texto de enlace basado en la columna de codigo. Para montos, usar una mascara declarativa como `FM999G999G999G999G990D00`. Evitar JavaScript de carga de pagina cuando la configuracion declarativa resuelve el requisito.
- En Interactive Reports, distinguir la definicion de desarrollo de las preferencias guardadas por cada usuario o sesion. Una sesion antigua puede conservar columnas u orden anteriores. Verificar los cambios en una sesion publica limpia usando la URL amigable y, cuando corresponda, `?session=0`; no agregar parametros de limpieza que infrinjan Page Access Protection o requieran checksum.
- En automatizacion del Page Designer, no confiar solo en que un campo visualmente lleno quedo registrado. Usar eventos reales de teclado o controles accesibles, salir del campo para confirmar el cambio, volver a seleccionar la columna y verificar el valor antes de guardar. Confirmar el mensaje `Changes saved` y que el boton Save quede deshabilitado.
- Validar la aplicacion publicada de extremo a extremo: cantidad de regiones y filas esperadas, encabezados y orden, formatos, enlaces externos, respuesta de cada REST Data Source y ausencia de columnas tecnicas visibles no solicitadas. Probar tanto una sesion limpia como el comportamiento esperado de una sesion existente.
- Para aplicaciones corporativas, preferir SSO Oracle o el esquema corporativo aprobado. El acceso publico solo debe usarse como transicion consciente para datos aptos para publicacion, documentando el plan de cierre. Tratar `identity.oraclecloud.com` y `signon-int.oracle.com` como pantallas SSO y detener la automatizacion para que Martin complete autenticacion o MFA cuando sea necesario.
- Publicar enlaces estables sin identificadores de sesion, tokens ni cookies. Nunca guardar en codigo o `AGENTS.md` contrasenas del workspace, credenciales REST, API keys, cookies SSO ni valores de `.env`.
- En este equipo, APEX corporativo puede requerir VPN, mientras que SSH hacia OCI puede fallar al usarla. Separar las etapas: usar VPN para APEX/SSO y, si la evidencia muestra timeout SSH asociado a la VPN, desconectarla solo para el despliegue autorizado y reconectarla para la configuracion y validacion de APEX. No cambiar reglas de red a ciegas.
