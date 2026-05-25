# Resumen Escolar - contexto de diagnostico para LLM

Fecha de este README: 2026-05-11  
Proyecto local: `C:\Users\Martin\Documents\Codex\resumen_escolar`  
Archivo principal: `C:\Users\Martin\Documents\Codex\resumen_escolar\resumen_escolar\app.py`  
Aplicacion local: `http://127.0.0.1:8765/`

## Objetivo de la app

Resumen Escolar es una app local en Python que usa Playwright con Chrome controlado para leer evidencia visible desde:

- SchoolNet: calificaciones y conducta.
- Google Classroom: curso 4-A, seccion Trabajo de clase.

Con esa evidencia genera un archivo `prompt_chatgpt.txt` para pedirle a ChatGPT una infografia escolar visual sobre el estudiante Gabito Garcia.

El prompt final debe pedir una unica salida principal:

- una sola imagen/infografia visual;
- sin texto copiable aparte;
- sin pregunta final;
- sin segunda parte fuera de la imagen.

## Estado actual del problema

El prompt generado todavia no contiene la informacion completa de la prueba de Matematica del 26 de mayo.

La evidencia que se espera extraer desde Classroom aparece en la vista de Google Classroom del curso 4-A, Trabajo de clase, tema/asignatura MATEMATICAS. El item relevante se ve como una tarjeta/material con titulo:

```text
Prueba Unidad N°2 (26 DE MAYO)
```

Al abrir o desplegar ese item, Classroom muestra este texto relevante:

```text
MATEMÁTICAS
Material
book
Prueba Unidad N°2 (26 DE MAYO)
Publicado: Ayer

Queridos niños y niñas,
tal como lo agendamos, el próximo marte 26 de mayo tendremos una evaluación de la unidad 2. Para esta prueba el temario es el siguiente:
- Resolver problemas mediante la adición o sustracción utilizando diversas estrategias ( descomposición, pictórico y algorítmo) hasta el 100.000.
- Resolver operaciones mediante estrategias de cálculo mental.
- Estimar sumas y restas redondeando números.
- Identificar y resolver ecuaciones e inecuaciones.
Para practicar recuerda revisar tus libros y cuaderno.
Lección 5 a la 12 del libro y sus correspondiente prácticas del libro de práctica.

Cariños,

Miss Mariana
```

Ese bloque debe aparecer en la evidencia cruda del prompt porque es material clave para explicar de que se trata la prueba del 26 de mayo.

## Resultado esperado en el prompt

En `outbox/YYYY-MM-DD/prompt_chatgpt.txt`, dentro de la evidencia Classroom, deberia aparecer un bloque equivalente a:

```text
DETALLE DE POSTS RELEVANTES ABIERTOS EN GOOGLE CLASSROOM - 4-A - TRABAJO EN CLASE - TEMA: MATEMATICAS
...
Titulo: Prueba Unidad N°2 (26 DE MAYO)
...
Texto extraido al abrir el post:
Queridos niños y niñas,
...
Resolver problemas mediante la adición o sustracción...
Resolver operaciones mediante estrategias de cálculo mental.
Estimar sumas y restas...
Identificar y resolver ecuaciones e inecuaciones.
Lección 5 a la 12...
```

Si ese texto no aparece, ChatGPT responde correctamente "No detectado" cuando se le pregunta por el temario, porque el problema esta en la extraccion de Classroom, no en ChatGPT.

## Cambios realizados hasta ahora

### Prompt maestro

Se modifico el prompt maestro para que ChatGPT entregue una sola infografia visual:

- Se elimino la logica de "Parte 2".
- Se elimino "texto copiable y pegable".
- Se elimino la pregunta final sobre si se desea texto copiable.
- Se agrego la instruccion `SALIDA UNICA OBLIGATORIA`.
- Se pidio explicitamente que la respuesta sea una sola imagen/infografia visual.
- Se pidio no inventar datos y usar `No detectado` cuando falta evidencia.
- Se mantuvo el estudiante Gabito Garcia.
- Se mantuvo la fecha del reporte y fecha de corte.
- Se prioriza la evidencia cruda entregada por la app.

### SchoolNet

La extraccion de SchoolNet funciona relativamente bien:

- Calificaciones P1.
- Conducta.
- Detalle de anotaciones.
- Lectura estructurada de tablas cuando es posible.

### Classroom: intentos descartados

Se evaluo usar Gemini en Classroom, pero se descarto:

- No se encontro boton/caja de prompt de Gemini dentro de Classroom.
- No se debe usar Gemini.
- No se debe usar OAuth ni API de Google Classroom por ahora.
- La solucion debe ser solamente Playwright leyendo el DOM/navegador.

### Classroom: enfoque actual

Se cambio el enfoque para evitar navegacion generica y lenta:

- Curso fijo 4-A:

```text
COURSE_ID = ODQ5Nzk2MDk0NDk5
CLASSWORK_URL = https://classroom.google.com/w/ODQ5Nzk2MDk0NDk5/t/all
```

- La app debe ir directo a `CLASSWORK_URL`.
- No debe hacer clicks genericos en "Trabajo de clase", porque antes eso termino en:

```text
https://classroom.google.com/a/not-turned-in/all
```

- Solo se deben aceptar vistas reales de tema/asignatura con patron:

```text
https://classroom.google.com/w/ODQ5Nzk2MDk0NDk5/tc/...
```

- Se deben rechazar explicitamente rutas globales:

```text
/h
/calendar
/ai
/s
/a/not-turned-in
otros cursos /c/...
cursos archivados
ajustes
Gemini
Inicio
Calendar
Tareas pendientes
```

### Classroom: extraccion esperada

El extractor deberia:

1. Abrir `https://classroom.google.com/w/ODQ5Nzk2MDk0NDk5/t/all`.
2. Detectar links de temas/asignaturas `/tc/...`.
3. Filtrar solo temas reales, por ejemplo:
   - MATEMATICAS
   - INGLES
   - CIENCIAS SOCIALES
   - CIENCIAS NATURALES
   - Lenguaje
   - Religion
   - Musica
   - Artes
   - Tecnologia
   - Educacion Fisica
4. Abrir cada vista `/tc/...`.
5. Buscar tarjetas o bloques cuyo titulo contenga palabras relevantes:
   - prueba
   - evaluacion
   - test
   - control
   - examen
   - tarea
   - entrega
   - temario
   - guia
   - hoja de ruta
   - practice
6. Para cada item relevante, intentar:
   - abrir la URL de detalle si existe;
   - convertir URLs `/m/{id}` a `/m/{id}/details`;
   - si no existe URL, hacer click en el titulo/tarjeta;
   - si el click falla, usar el texto visible desplegado como fallback.
7. Guardar en evidencia:
   - titulo exacto;
   - seccion/fuente;
   - asignatura/tema;
   - fecha visible de publicacion;
   - fechas detectadas;
   - texto crudo;
   - links/adjuntos visibles.

## URL importante de ejemplo

El usuario confirmo que el detalle del item de Matematica puede alcanzarse con una URL de este estilo:

```text
https://classroom.google.com/c/ODQ5Nzk2MDk0NDk5/m/ODYzMjYzMTUyNTYz/details
```

Esta URL es un ejemplo del patron a soportar. No se debe codificar duro ese item, pero el extractor debe poder derivar `/details` desde links `/m/...` cuando aparezcan en la tarjeta.

## Logs problematicos anteriores

### Caso lento y mal filtrado

Playwright recorrio rutas globales como si fueran temas:

```text
Inicio -> https://classroom.google.com/h
Calendar -> https://classroom.google.com/calendar/this-week/course/all
Gemini -> https://classroom.google.com/ai
Ajustes -> https://classroom.google.com/s
Clases archivadas -> https://classroom.google.com/h/archived
```

Eso no debe ocurrir.

### Caso redireccion a tareas pendientes

En otro intento, el log mostro:

```text
Classroom: ubicar curso 4-A | url=https://classroom.google.com/
Classroom: abrir Trabajo de clase | url=https://classroom.google.com/a/not-turned-in/all
Classroom: leer Filtro por tema | temas=0
```

Eso tampoco debe ocurrir. Si aparece `/a/not-turned-in/all`, la app debe registrarlo como ruta global no permitida y no usar esa pagina como evidencia Classroom.

## Diagnostico agregado en el codigo

Se agregaron logs/estadisticas para Classroom:

- cantidad de temas detectados;
- URLs reales de tema;
- candidatos de posts relevantes;
- titulos candidatos;
- URLs de detalle candidatas;
- posts abiertos;
- abiertos por URL;
- abiertos por click;
- clicks fallidos;
- detalles vacios;
- saltados por antiguedad;
- saltados por fecha pasada.

El README y el zip incluyen el codigo para que otro LLM revise si esos contadores estan bien alimentados y si la evidencia se incorpora al prompt final.

## Archivos relevantes

```text
README.md
requirements.txt
install_deps.cmd
run_resumen_escolar.cmd
resumen_escolar/app.py
resumen_escolar/__init__.py
resumen_escolar/__main__.py
outbox/2026-05-08/prompt_chatgpt.txt
```

No se incluye `.runtime/` porque contiene dependencias instaladas, perfil de Chrome y archivos grandes/no necesarios para diagnostico de codigo.

## Como ejecutar

Desde PowerShell:

```powershell
Set-Location "C:\Users\Martin\Documents\Codex\resumen_escolar"
& ".\run_resumen_escolar.cmd"
```

La app abre:

```text
http://127.0.0.1:8765/
```

Flujo manual:

1. Abrir la app local.
2. Presionar `Abrir plataformas`.
3. Confirmar sesion en SchoolNet y Classroom.
4. Presionar `Generar prompt ChatGPT`.
5. Revisar `outbox/YYYY-MM-DD/prompt_chatgpt.txt`.

## Comandos de validacion rapida

Validar sintaxis sin generar `__pycache__`:

```powershell
Set-Location "C:\Users\Martin\Documents\Codex\resumen_escolar"
@'
import ast
from pathlib import Path
path = Path("resumen_escolar/app.py")
ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print("syntax ok")
'@ | python -B -
```

Buscar si el prompt generado contiene el temario esperado:

```powershell
Select-String -Path ".\outbox\2026-05-08\prompt_chatgpt.txt" -Pattern "Prueba Unidad|Resolver problemas|calculo mental|cálculo mental|sumas y restas|ecuaciones e inecuaciones|Leccion 5|Lección 5" -Context 2,4
```

## Preguntas concretas para el LLM que diagnostique

1. Por que `prompt_chatgpt.txt` no incluye el texto completo del item `Prueba Unidad N°2 (26 DE MAYO)`?
2. La funcion que descubre links `/tc/...` esta leyendo los elementos correctos de Classroom?
3. La funcion que detecta candidatos relevantes esta encontrando la tarjeta de Matematica?
4. La funcion que normaliza links `/m/...` a `/details` esta recibiendo el href correcto?
5. El extractor de detalles se esta invocando en el flujo real de `_snapshot_classroom_page`?
6. Hay algun filtro de fecha que pueda estar descartando erroneamente un post publicado "Ayer" pero con prueba futura el 26 de mayo?
7. El texto extraido se esta truncando antes de llegar a la seccion de Matematica?
8. La evidencia Classroom se esta incorporando al prompt final con prioridad suficiente?
9. Hay selectores DOM demasiado generales o demasiado restrictivos para Classroom?
10. Conviene leer directamente `document.body.innerText` de cada vista `/tc/...` antes de intentar abrir tarjetas, dado que algunas vistas ya muestran el cuerpo desplegado?

## Criterios de exito

El arreglo se considera correcto cuando:

- el log no muestra navegacion a `Gemini`, `Calendar`, `Inicio`, `Ajustes`, `Clases archivadas` ni `/a/not-turned-in/all`;
- se detecta la vista de tema MATEMATICAS;
- se detecta el titulo `Prueba Unidad N°2 (26 DE MAYO)`;
- se abre o captura el detalle de esa tarjeta;
- el prompt incluye:
  - `Resolver problemas mediante la adición o sustracción`;
  - `cálculo mental`;
  - `sumas y restas`;
  - `ecuaciones e inecuaciones`;
  - `Lección 5 a la 12`;
- ChatGPT puede responder de que se trata la prueba del 26 de mayo usando evidencia cruda;
- el output pedido a ChatGPT sigue siendo solo una imagen/infografia visual.

