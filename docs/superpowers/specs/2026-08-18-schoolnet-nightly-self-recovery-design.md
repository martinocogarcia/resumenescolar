# Diseño: autorrecuperación nocturna de SchoolNet

Fecha: 2026-08-18  
Estado: aprobado en conversación; pendiente de revisión del archivo por Martin

## 1. Objetivo

Conseguir que el cron diario de Resumen Escolar se recupere automáticamente de fallos técnicos de login, página, CDP, Chromium, red, extracción y publicación durante una ventana nocturna de 03:00 a 06:30, hora de Chile.

Una ejecución solo se considera exitosa cuando SchoolNet está autenticado, la extracción completa termina y el reporte queda publicado. Que el cron se inicie, CDP responda o Chromium se reinicie no constituye éxito por sí solo.

La automatización debe detenerse inmediatamente ante contraseña rechazada, cuenta bloqueada, CAPTCHA, MFA o verificación manual. No debe intentar eludir controles de autenticación ni arriesgar el bloqueo de la cuenta.

## 2. Evidencia y causa raíz

Los logs entre el 12 y el 18 de agosto de 2026 muestran siete fallos nocturnos consecutivos con el mismo patrón:

- Chromium/CDP estaba disponible y reutilizaba el perfil persistente.
- OCI Vault entregaba una credencial con usuario y contraseña presentes.
- La pestaña permanecía en `https://schoolnet.colegium.com/webapp/es_CL/login`.
- El código no encontraba los campos visibles de usuario y contraseña.
- Desde el 13 de agosto se ejecutaron seis intentos durante aproximadamente una hora, todos contra el mismo estado de página.
- No aparecieron errores técnicos de Chromium.
- Los hashes de los cuatro archivos principales desplegados coincidían con los archivos locales.
- La página pública actual de SchoolNet sí expone un formulario normal con Usuario, Contraseña, Recuérdame e Iniciar sesión.

La causa raíz está en el control de recuperación: cuando existe una pestaña cuya URL contiene `schoolnet`, el flujo la reutiliza sin asegurar que su DOM esté listo. Si no encuentra el formulario, los siguientes intentos vuelven a evaluar la misma pestaña sin recargarla, navegar nuevamente, reemplazarla ni renovar la sesión CDP. Los reintentos consumen tiempo, pero no producen una transición de estado capaz de resolver el fallo.

## 3. Alcance

Incluido:

- recuperación escalonada de página, pestaña, conexión CDP y Chromium;
- reintentos técnicos hasta las 06:30;
- detección de sesión manual/noVNC antes de reiniciar Chromium;
- tratamiento independiente de fallos de Vault, red, extracción y publicación;
- logs humanos y JSONL detallados;
- notificación OCI autocontenida y segura para copiar en Codex;
- pruebas unitarias, pruebas de integración acotadas y verificación remota;
- despliegue con preservación de estado y rollback del código.

Excluido:

- cambiar contraseñas o el secreto de Vault;
- resolver o eludir CAPTCHA, MFA o verificaciones manuales;
- modificar IAM, Security Lists, NSG, firewall, VPN o DNS;
- borrar perfiles, cookies, caches, reportes o datos persistentes;
- garantizar recuperación automática de fallos desconocidos que requieran una decisión humana o cambios externos.

## 4. Arquitectura

### 4.1 Controlador de recuperación

Se incorporará un controlador Python aislado y testeable para dirigir la escalera de recuperación. No contendrá credenciales ni datos extraídos. Recibirá señales técnicas del navegador y devolverá la próxima acción permitida.

Responsabilidades:

- mantener `RUN_ID`, ciclo, etapa, deadline y enfriamientos;
- clasificar el fallo actual;
- seleccionar una única acción de recuperación por transición;
- registrar entrada, salida, duración y motivo de cada decisión;
- detenerse ante una condición no automatizable;
- evitar repetir una acción que no cambió ninguna señal observable.

### 4.2 Adaptador de navegador

`BrowserController` expondrá operaciones pequeñas con resultados estructurados:

- inspeccionar página y formulario sin leer valores;
- esperar una condición de DOM;
- recargar;
- navegar explícitamente al login;
- cerrar solo la pestaña SchoolNet defectuosa y crear una nueva;
- descartar y reconstruir la conexión Playwright/CDP;
- verificar que SchoolNet salió del login;
- ejecutar la extracción completa.

La selección de página no aceptará ciegamente la primera URL que contenga `schoolnet`. Evaluará salud, estado del documento y señales del formulario o sesión autenticada.

### 4.3 Guardián de Chromium

El guardián conservará su conducta no destructiva por defecto. Se agregará una operación explícita de reinicio seguro que:

- confirme que no existe una sesión manual/noVNC activa;
- confirme que el proceso pertenece al perfil de Resumen Escolar;
- cierre Chromium ordenadamente y espere su salida;
- no borre locks, perfil, cookies ni datos;
- reinicie Chromium con el mismo perfil y CDP local;
- valide CDP y la existencia de una pestaña navegable;
- se ejecute como máximo una vez cada 20 minutos.

Si hay una sesión noVNC activa, el controlador no podrá reiniciar Chromium. Podrá actuar sobre pestañas y, si eso no basta, terminará con una notificación que indique esta restricción.

### 4.4 Runner nocturno

El shell runner conservará un único lock. Invocará el orquestador y mantendrá latidos visibles. El proceso podrá iniciar nuevos ciclos técnicos hasta las 06:30, pero nunca superpondrá dos extracciones.

Cuando el proceso Python termine inesperadamente, el runner podrá iniciar otro ciclo dentro de la ventana. El estado del ciclo anterior quedará registrado para evitar reiniciar desde cero sin contexto.

## 5. Flujo de recuperación

La escalera se ejecutará en este orden:

1. Validar deadline, configuración mínima y espacio disponible.
2. Validar acceso a Vault sin imprimir el secreto.
3. Validar CDP y perfil.
4. Inspeccionar la pestaña SchoolNet y esperar el formulario o una señal autenticada.
5. Si ya está autenticada, pasar directamente a la extracción. Si el formulario está listo, intentar login desde Vault y confirmar que salió del login.
6. Si la página no está lista, recargarla y volver al paso 4.
7. Si la recarga no cambia las señales, navegar explícitamente al login y volver al paso 4.
8. Si la navegación no cambia las señales, cerrar únicamente la pestaña SchoolNet defectuosa, crear una nueva y volver al paso 4.
9. Si la pestaña nueva no cambia las señales, desconectar y reconectar Playwright a CDP y volver al paso 4.
10. Si la conexión sigue degradada, reiniciar Chromium de forma segura cuando no haya noVNC activo y haya terminado el enfriamiento; después volver al paso 4.
11. Ejecutar la extracción completa una vez confirmada la autenticación.
12. Publicar reporte y estado.
13. Publicar la notificación de éxito.

Después de cada acción técnica se vuelve a inspeccionar antes de escalar. Si aparece el formulario, se intenta el login inmediatamente; no se ejecutan niveles de recuperación innecesarios. Si una acción cambia las señales pero aún no obtiene éxito, el controlador continúa desde la inspección. Si no cambia ninguna señal, escala a la siguiente acción. Al llegar a las 06:30 termina limpiamente y publica el diagnóstico final.

## 6. Clasificación de fallos

| Categoría | Ejemplos | Tratamiento |
|---|---|---|
| `page_not_ready` | formulario ausente, documento incompleto | esperar, recargar, navegar, pestaña nueva |
| `cdp_unavailable` | conexión rechazada, contexto ausente | guardián, reconexión, reinicio seguro |
| `browser_degraded` | proceso vivo sin página utilizable | reinicio seguro con enfriamiento |
| `network_transient` | DNS, TLS, timeout de navegación | espera creciente y nueva navegación |
| `vault_transient` | OCI CLI o servicio temporalmente inaccesible | reintentar solo Vault |
| `vault_invalid` | secreto ausente o JSON inválido | detener y notificar |
| `auth_rejected` | credencial explícitamente rechazada | detener inmediatamente |
| `manual_required` | CAPTCHA, MFA, bloqueo o verificación | detener inmediatamente |
| `extraction_changed` | login correcto, DOM de datos no interpretable | recargar y repetir una vez; luego notificar |
| `publish_transient` | Object Storage o Notifications temporalmente falla | conservar salida y reintentar solo publicación |
| `configuration_error` | dependencia, variable o espacio insuficiente | detener sin borrar datos |
| `deadline_reached` | son las 06:30 | terminar y notificar |

Una falla de extracción no debe convertirse en un ciclo de login. Una falla de publicación no debe volver a ejecutar Playwright.

## 7. Observabilidad

### 7.1 Log humano

El log diario tendrá una línea por transición con, al menos:

- timestamp con zona horaria;
- `RUN_ID`;
- número de ciclo e intento;
- etapa y acción;
- resultado;
- duración;
- categoría de fallo;
- señales técnicas resumidas;
- próxima decisión.

### 7.2 Log JSONL

Cada transición escribirá un objeto JSON independiente. Campos estables:

- `timestamp`, `run_id`, `cycle`, `attempt`;
- `stage`, `action`, `result`, `duration_ms`;
- `failure_category`, `stop_reason`, `next_action`;
- `url_host`, `url_path`, `document_state`;
- `page_count`, `frame_count`, `visible_input_count`;
- `username_field_present`, `password_field_present`, `submit_present`;
- `vault_status`, `cdp_status`, `profile_status`, `novnc_active`;
- `browser_restart_count`, `deadline`;
- `report_generated`, `report_published`, `notification_published`;
- `safe_message`.

No se escribirán valores de inputs, credenciales, cookies, HTML, tokens ni datos personales.

### 7.3 Email OCI

El asunto identificará resultado, categoría y etapa final. El cuerpo incluirá:

- resumen del resultado;
- inicio, fin, duración y deadline;
- versión desplegada;
- estado de cron, Vault, CDP, perfil y noVNC;
- etapa exacta donde terminó;
- recorrido completo de acciones y resultados;
- último reporte válido preservado;
- ruta del log remoto;
- bloque estable `DIAGNOSTICO PARA CODEX` listo para copiar.

El cuerpo respetará el límite de OCI Notifications. Si debe recortarse, conservará siempre el resumen, la etapa fallida, la causa, los primeros eventos, los últimos eventos y cualquier evento no repetitivo. Solo se compactarán repeticiones equivalentes.

La redacción eliminará correos, datos personales, credenciales, valores de formulario, cookies, HTML, tokens, OCID, IP y rutas de llaves.

## 8. Pruebas

El desarrollo será guiado por pruebas. Casos mínimos:

1. URL de login con cero campos: debe escalar y no repetir seis veces la misma acción.
2. Formulario que aparece después de esperar: debe continuar sin recarga innecesaria.
3. Recarga que restaura el formulario: debe iniciar sesión y detener la escalera.
4. Pestaña nueva que restaura el formulario: debe cerrar solo la defectuosa.
5. CDP desconectado: debe reconectar o solicitar reinicio seguro.
6. noVNC activo: debe bloquear el reinicio de Chromium.
7. reinicios repetidos: debe aplicar enfriamiento de 20 minutos.
8. contraseña rechazada: debe detenerse sin nuevo intento.
9. CAPTCHA o MFA: debe detenerse y clasificar `manual_required`.
10. cambio de DOM de extracción: no debe clasificarse como login.
11. publicación fallida: debe reintentar publicación sin repetir extracción.
12. deadline de 06:30: debe terminar y notificar.
13. email: debe contener etapa exacta y recorrido copiable.
14. redacción: no debe filtrar secretos, correos, HTML, OCID, IP o datos personales.
15. éxito: exige autenticación, extracción y publicación.

Se ejecutarán pruebas unitarias, `git diff --check`, validación sintáctica de Python y Bash, y una prueba remota en segundo plano sin publicación.

## 9. Despliegue y verificación

El despliegue usará `scripts/deploy_to_oracle_form_vm.ps1` y el destino `/opt/resumen-escolar`. Debe preservar:

- `/opt/resumen-escolar/config/automation.env`;
- `.runtime/chrome-profile`;
- cache de evidencia;
- `outbox`;
- logs;
- cualquier estado de publicación válido.

Etapas verificables:

1. preflight local y estado Git;
2. pruebas locales;
3. paquete y backup remoto;
4. resolución de destino;
5. conexión y autenticación SSH;
6. carga;
7. instalación sin reemplazar configuración persistente;
8. hashes y sintaxis remotos;
9. cron, Vault y CDP;
10. prueba funcional sin publicación mediante proceso en segundo plano;
11. corrida controlada con publicación si la prueba anterior tiene éxito;
12. verificación independiente del resultado.

El despliegue no prueba por sí solo el éxito sostenido. El cron de la noche siguiente será la validación definitiva y su email debe mostrar la nueva traza estructurada.

## 10. Rollback

Antes del despliegue se conservará una copia recuperable del código y scripts remotos reemplazados. El rollback restaurará únicamente esos archivos y reiniciará el flujo con el mismo entorno.

Nunca se eliminarán ni revertirán el perfil Chromium, `automation.env`, cache, reportes o logs. Si el rollback no restaura una corrida funcional, se conservará el último reporte válido y se detendrá la automatización con diagnóstico seguro.

## 11. Criterios de aceptación

- El caso observado durante siete noches ya no repite una acción sin transición.
- Cada fallo indica etapa, acción, resultado, duración, categoría y próxima decisión.
- El email puede copiarse en Codex y permite reconstruir el recorrido sin acceder a la VM.
- No se filtra información sensible o personal.
- Chromium solo se reinicia si no hay una sesión noVNC activa y se preserva el perfil.
- Los controles de autenticación manual detienen la automatización.
- La ejecución finaliza a más tardar a las 06:30.
- Un fallo de publicación no repite extracción.
- El último reporte válido permanece disponible ante cualquier fallo.
- Una corrida se considera exitosa únicamente después de autenticación, extracción y publicación verificadas.
