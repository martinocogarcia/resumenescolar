# Resumen Escolar

Aplicacion local en Python para recopilar evidencia escolar visible y generar un resumen diario de texto para correo, con calificaciones, conducta y agenda.

Este repositorio no debe contener sesiones de navegador, credenciales, prompts generados ni evidencia personal extraida. Esos datos viven en carpetas locales ignoradas por Git.

## Como Opera

1. La app levanta un servidor local en `http://127.0.0.1:8765/`.
2. Playwright abre un navegador persistente usando un perfil local en `.runtime/chrome-profile`.
3. El usuario inicia sesion manualmente en las plataformas cuando sea necesario.
4. La app extrae evidencia visible de:
   - SchoolNet: calificaciones y conducta.
   - Google Classroom: seccion Trabajo de clase del curso configurado.
   - Calendario SSCC Segundo Ciclo: eventos del calendario oficial que mencionan `4A`.
5. La evidencia se normaliza en snapshots internos.
6. Un cache local incremental conserva registros historicos compactos en `.runtime/evidence_cache/evidence_store.json`.
7. El generador arma `daily_report.txt` y `daily_report_state.json` en `outbox/YYYY-MM-DD/`.
8. Si detecta adjuntos recientes o relacionados con evaluaciones futuras, genera un paquete desechable en `outbox/YYYY-MM-DD/materials/`.
9. La automatizacion publica el reporte por OCI Notifications y conserva respaldos en Object Storage.

## Flujo De Extraccion

### SchoolNet - Calificaciones

- Se leen calificaciones visibles.
- La infografia debe usar la columna P1 como promedio del primer semestre por asignatura.
- La infografia tambien debe incluir la columna P2 como promedio del segundo semestre por asignatura.
- Si P2 no tiene datos porque el segundo semestre aun no inicia o no hay notas visibles, la celda P2 debe quedar en blanco.
- El detalle interno de cada asignatura se conserva como evidencia para explicar de donde viene una nota.
- Las notas parciales no deben reemplazar los promedios P1 o P2.

### SchoolNet - Conducta

- La app intenta abrir las vistas de anotaciones positivas, negativas y neutras.
- Se capturan tablas visibles con columnas como fecha, motivo, profesor, asignatura, observaciones y categoria.
- Para describir una anotacion en la infografia, el prompt prioriza el campo Observaciones.
- El campo Motivo se conserva como clasificacion tecnica o reglamentaria, no como texto principal de la anotacion.

### Google Classroom - Trabajo De Clase

- La app navega directo a la vista Trabajo de clase del curso configurado.
- Se recorren temas/asignaturas validas mediante URLs de tema.
- Se ignoran rutas globales como calendario, inicio, Gemini, tareas pendientes, ajustes o cursos archivados.
- Se buscan tarjetas relevantes por palabras clave como prueba, evaluacion, control, test, examen, tarea, guia, temario, hoja de ruta o practice.
- Para tarjetas relevantes, la app intenta abrir el detalle o convertir enlaces de material/asignacion a su vista `/details`.
- Se extraen titulo, tema/asignatura, fechas visibles, texto crudo, links y adjuntos visibles.
- Actividades con fecha anterior a la fecha de corte no deben mostrarse como tareas/evaluaciones proximas.
- La lectura de Playwright es incremental y liviana: puede limitar la busqueda viva a posts recientes, pero el TXT generado debe incluir tambien el cache historico de Classroom con toda la informacion relevante ya levantada en corridas anteriores.

### Calendario SSCC Segundo Ciclo

- La app lee primero el feed iCal publico del calendario de evaluaciones 4A:
  `c_aejfpaujkj4nm4u6kfbsc4eu2g@group.calendar.google.com`.
- Extrae eventos estructurados que mencionen `4A`, `4 A`, `4-A`, `4°A` o variantes equivalentes.
- Si el feed iCal falla, mantiene como respaldo la lectura visual del calendario embebido en `https://ssccmanquehue.cl/calendario-segundo-ciclo`.
- Esos eventos se agregan al prompt como fuente oficial adicional de fechas de tareas, pruebas, controles, salidas y actividades.
- La app filtra el calendario antes de armar el prompt: solo incluye eventos desde la fecha del reporte hasta un mes hacia adelante.

## Prompt Para ChatGPT

El prompt maestro instruye a ChatGPT para:

- usar solo la evidencia cruda incluida;
- no inventar datos;
- escribir `No detectado` si falta informacion;
- producir una sola imagen/infografia visual;
- no agregar texto antes ni despues de la imagen;
- mantener fecha del reporte y fecha de corte;
- excluir pruebas, controles, tareas o evaluaciones pasadas;
- incluir material de estudio relacionado con evaluaciones futuras;
- usar un titulo sugerido de chat con fecha, para que el historial de ChatGPT sea buscable.

El titulo sugerido tiene esta forma:

```text
Resumen Escolar - <estudiante> - YYYY-MM-DD
```

## Estructura Principal

```text
resumen_escolar/
  app.py          # servidor local, Playwright, extractores y prompt builder
  __main__.py     # entrypoint python -m resumen_escolar
  __init__.py

requirements.txt # dependencia principal: playwright
install_deps.cmd # instala dependencias en .runtime/site-packages
run_resumen_escolar.cmd # levanta la app local
```

## Carpetas Locales Sensibles

Estas carpetas no deben subirse ni compartirse:

```text
.runtime/
outbox/
diagnostic_bundle_*/
*.zip
```

Detalles:

- `.runtime/chrome-profile` contiene cookies, sesiones, caches del navegador y debe tratarse como credencial.
- `.runtime/evidence_cache` contiene evidencia historica extraida y puede incluir datos personales.
- `outbox/` contiene prompts generados y diagnosticos con evidencia escolar.
- bundles diagnosticos y zips pueden contener copias de prompts o codigo en estados anteriores.

## Instalacion Local

Requisitos:

- Windows con Python 3.11.
- Chrome o Edge instalado en las rutas esperadas por `app.py`.

Instalar dependencias:

```powershell
Set-Location "C:\ruta\al\proyecto"
& ".\install_deps.cmd"
```

Levantar la app:

```powershell
Set-Location "C:\ruta\al\proyecto"; $env:TMP="C:\ruta\al\proyecto\.runtime\temp"; $env:TEMP=$env:TMP; New-Item -ItemType Directory -Force $env:TEMP | Out-Null; & ".\run_resumen_escolar.cmd"
```

Abrir:

```text
http://127.0.0.1:8765/
```

## Configuracion A Revisar

El proyecto usa constantes en `app.py` para identificar el estudiante y el curso de Classroom. Antes de compartir o reutilizar el proyecto, reemplazar esos valores por placeholders o configuracion externa.

Ejemplos de valores que no conviene publicar:

- nombre real del estudiante;
- ID real del curso de Classroom;
- perfiles de navegador;
- evidencia historica;
- prompts generados;
- credenciales o tokens.

## Pruebas Recomendadas

Validar sintaxis:

```powershell
python -m compileall -q .
```

Probar arranque local:

```powershell
Set-Location "C:\ruta\al\proyecto"; $env:TMP="C:\ruta\al\proyecto\.runtime\temp"; $env:TEMP=$env:TMP; New-Item -ItemType Directory -Force $env:TEMP | Out-Null; & ".\run_resumen_escolar.cmd"
```

Luego abrir `http://127.0.0.1:8765/`.

La prueba completa de Playwright debe hacerse con el usuario presente, porque puede requerir sesiones activas o login manual.

## Publicacion Y Seguridad

Antes de commitear o compartir:

```powershell
git status --short --ignored
git ls-files
```

Confirmar que no aparecen:

- `.runtime/`
- `outbox/`
- `.env`
- bases SQLite o DB locales;
- logs;
- zips diagnosticos;
- llaves privadas;
- tokens;
- credenciales JSON.

## Notas De Mantenimiento

- `site-packages` dentro de `.runtime` es recreable con `install_deps.cmd`.
- `chrome-profile` no debe borrarse salvo que se quiera forzar relogin completo.
- `evidence_cache` mejora la eficiencia incremental, pero contiene datos extraidos.
- Si Playwright falla por directorio temporal, crear `.runtime/temp` y asignar `TMP`/`TEMP` antes de ejecutar.

## Automatizacion Diaria En OCI

Objetivo: ejecutar el flujo en la VM `oracle-form-app-vm`, generar el reporte diario y publicarlo por OCI Notifications y Object Storage como:

```text
latest/daily_report.txt
latest/daily_report_state.json
archive/YYYY-MM-DD/daily_report.txt
archive/YYYY-MM-DD/daily_report_state.json
```

El publicador usa OCI CLI. En la VM se recomienda `RESUMEN_ESCOLAR_OCI_AUTH=instance_principal`, con permisos IAM sobre el bucket privado. Asi no se guardan API keys, fingerprints ni archivos `.oci/config` dentro del repositorio.

 Opcionalmente, el runner puede enviar una notificacion al publicar el TXT y tambien cuando falla antes de publicar. Para activarlo, crear un topic en OCI Notifications, suscribir un email y confirmar la suscripcion; luego definir `RESUMEN_ESCOLAR_NOTIFICATION_TOPIC_OCID` en `/opt/resumen-escolar/config/automation.env`.

 El correo de exito contiene cambios semanticos y el estado completo. Al final incluye un bloque delimitado por RESUMEN_ESCOLAR_JSON_BEGIN y RESUMEN_ESCOLAR_JSON_END con JSON UTF-8 valido para que una conversacion de ChatGPT conectada a Outlook pueda localizar el correo mas reciente y responder preguntas sin cargas manuales. En `grades`, cada asignatura conserva sus promedios `p1` y `p2` e incluye `assessments` con cada fila evaluada visible al abrir esa asignatura en SchoolNet: fecha, titulo, todas las notas de la fila y otros datos de contexto. `grade_details` informa cuantas evaluaciones y asignaturas con evaluaciones se incluyeron. La fecha `report_date` identifica la corrida. El estado se compara contra la ultima ejecucion exitosa y el cuerpo completo se limita a 55 KB para respetar OCI Notifications.

Las alertas de fallo estan activas por defecto cuando existe `RESUMEN_ESCOLAR_NOTIFICATION_TOPIC_OCID`. Si SchoolNet o Google Classroom quedan en login, el correo identifica la plataforma, incluye el comando PowerShell de recuperacion con el modo correcto (`SchoolNet`, `Classroom` o `Both`) y deja un log local de la sesion. Debe ejecutarse temporalmente sin la VPN corporativa, porque la VPN puede bloquear SSH hacia la VM. Para apagar alertas de fallo, definir `RESUMEN_ESCOLAR_NOTIFY_FAILURES=0`.

### Configuracion Esperada En La VM

Archivo privado sugerido:

```text
/opt/resumen-escolar/config/automation.env
```

Usar `.env.example` como plantilla. No commitear valores reales si contienen rutas locales sensibles, nombres privados de bucket o URLs PAR.

Variables principales:

```text
RESUMEN_ESCOLAR_BUCKET=<bucket-privado>
RESUMEN_ESCOLAR_OCI_REGION=ca-toronto-1
RESUMEN_ESCOLAR_OCI_AUTH=instance_principal
RESUMEN_ESCOLAR_HEADLESS=1
RESUMEN_ESCOLAR_BROWSER_EXE=playwright
RESUMEN_ESCOLAR_MATERIALS_LATEST_PREFIX=latest/materials
RESUMEN_ESCOLAR_MATERIALS_ARCHIVE_TEMPLATE=archive/{date}/materials
RESUMEN_ESCOLAR_SCHOOLNET_SECRET_OCID=<ocid-del-secret-vault>
RESUMEN_ESCOLAR_NOTIFY_FAILURES=1
```

### Despliegue De Actualizaciones

El despliegue normal usa `scripts/deploy_to_oracle_form_vm.ps1`. Verifica que la
VM tenga `python3`, `venv`, `tar`, `curl` y `unzip`, pero no ejecuta `apt update`
ni instala paquetes del sistema. Asi una actualizacion de Python no queda
esperando repositorios de Ubuntu que no responden.

En una VM nueva que realmente no tenga esos prerrequisitos, usar una sola vez
el parametro `-BootstrapSystemPackages`. Ese modo habilita `apt` con un timeout
de 30 segundos y un unico reintento, e instala las dependencias de sistema de
Playwright; no se usa en despliegues rutinarios.

### Materiales Recientes De Classroom

El runner puede conservar materiales de Classroom como respaldo interno. No son
necesarios para el correo diario y no se publican por defecto.

Salida local:

```text
outbox/YYYY-MM-DD/materials/materials_summary.txt
outbox/YYYY-MM-DD/materials/materials_index.json
outbox/YYYY-MM-DD/materials/files/<archivo>
```

Los archivos originales son respaldo visual opcional. V1
no hace OCR: si una imagen/PDF no entrega texto visible, queda marcado como
`texto_no_detectado` y no se debe inventar contenido.

La corrida puede abrir solo posts recientes para
mantenerse liviana, pero el TXT conserva los posts relevantes ya levantados
historicamente como memoria de respaldo; esos posts antiguos no deben tratarse
como agenda vigente si sus fechas ya pasaron.

### Credenciales SchoolNet En OCI Vault

El login automatico de SchoolNet usa OCI Vault cuando esta configurado
`RESUMEN_ESCOLAR_SCHOOLNET_SECRET_OCID`. El secreto debe contener JSON UTF-8:

```json
{
  "username": "usuario-schoolnet",
  "password": "clave-schoolnet"
}
```

El runner lee el secreto con OCI CLI e `instance_principal`, deja usuario/clave solo
en variables de entorno del proceso y no imprime esos valores. Classroom reutiliza
la sesion Google del perfil Chromium; cuando expira, puede iniciar sesion por la
interfaz web si `RESUMEN_ESCOLAR_GOOGLE_SECRET_OCID` apunta a otro secreto JSON
con `username` y `password`. El secreto de Google se crea y configura directamente
en OCI, sin copiar la clave al repositorio, al chat ni a los logs. Si Google exige
MFA, CAPTCHA o bloquea el navegador automatizado, el cron se detiene, preserva el
ultimo reporte valido y envia una alerta operacional.

El cron usa la sesion persistente solo como optimizacion y no como fuente de
contraseñas: antes de cada corrida ejecuta `scripts/ensure_chromium_cdp.sh`. Si
Chromium ya expone CDP, reutiliza esa sesion; si no hay navegador, inicia uno
headless con el mismo perfil y CDP local. Si el perfil esta ocupado sin CDP, se
detiene sin matar procesos ni borrar locks, y la alerta se clasifica como problema
de navegador, no como fallo de SchoolNet.

Cuando SchoolNet muestra de verdad la pantalla de login, la app recupera el secreto
desde Vault en memoria, realiza un solo intento automatico y verifica que salio del
login. Las alertas distinguen: credencial/Vault no disponible, login rechazado,
CDP/perfil ocupado y errores generales. Solo los dos primeros casos relacionados
con SchoolNet deben pedir intervencion sobre la cuenta.

Permiso IAM sugerido para la dynamic group de la VM:

```text
Allow dynamic-group resumen_escolar_gabitin_publishers to read secret-bundles in compartment <compartment>
```

Si tambien se usa la misma dynamic group para publicar el prompt, mantener la policy
de Object Storage del bucket privado. Para restringir mas, usar OCIDs de compartment,
vault o secret segun la politica IAM disponible en la tenancy.

Fallback solo para pruebas locales:

```text
RESUMEN_ESCOLAR_SCHOOLNET_USERNAME=<usuario>
RESUMEN_ESCOLAR_SCHOOLNET_PASSWORD=<clave>
```

No guardar esas dos variables con valores reales en Git, logs, paquetes de deploy ni
archivos compartidos.

Verificar desde la VM que el secreto se puede leer sin imprimir credenciales:

```bash
/opt/resumen-escolar/scripts/test_schoolnet_vault_secret.sh
```

### Runner No Interactivo

Generar sin publicar:

```bash
python3 -m resumen_escolar.automation run
```

Generar y publicar el reporte diario y su estado:

```bash
python3 -m resumen_escolar.automation run --publish
```

El reporte se guarda como `outbox/YYYY-MM-DD/daily_report.txt` y el estado como
`outbox/YYYY-MM-DD/daily_report_state.json`. La publicación diaria usa
`latest/daily_report.txt`, `latest/daily_report_state.json` y sus copias bajo
`archive/YYYY-MM-DD/`. El correo de OCI Notifications contiene únicamente
texto plano listo para copiar y pegar; no incluye URLs PAR, rutas locales ni
instrucciones para ChatGPT.

Publicar un prompt legacy ya existente (compatibilidad):

```bash
python3 -m resumen_escolar.automation publish --prompt-path outbox/YYYY-MM-DD/prompt_chatgpt.txt
```

El runner no imprime el contenido del prompt. Solo reporta ruta, tamaño, estados de snapshots y resultado de subida. Por defecto falla antes de publicar si detecta estados bloqueados como login requerido o error de lectura.

### Cron

Script principal preparado:

```text
scripts/run_daily_report.sh
```

`scripts/run_weekly_prompt.sh` se conserva como wrapper compatible.

El runner deja etapas `[1/8]` en `logs/daily-report-YYYY-MM-DD.log`, un latido
cada minuto mientras la extraccion sigue activa y una advertencia visible al
superar 25 minutos. El cierre siempre registra el `exit` real y los segundos
transcurridos; una advertencia de 25 minutos no interrumpe por si sola el cron.

Cron recomendado: todos los dias a las 03:00 hora local de Santiago.

```cron
CRON_TZ=America/Santiago
0 3 * * * RESUMEN_ESCOLAR_APP_DIR=/opt/resumen-escolar /opt/resumen-escolar/scripts/run_daily_report.sh
```

La VM esta en UTC; si se quiere fijar estrictamente a GMT-4 sin depender de cambios de horario de Chile, el equivalente es `0 7 * * *` sin `CRON_TZ`.

El script usa lock para evitar ejecuciones simultaneas y escribe logs en:

```text
/opt/resumen-escolar/logs/daily-report-YYYY-MM-DD.log
```
