SC HUB - recolector de comercio

Descarga cada 15 min los precios de UEX Corp, calcula las mejores compras y ventas por mercancia y publica data/commodities.json y data/meta.json en GitHub Pages.

Puesta en marcha
Crea un repositorio en GitHub y sube estos archivos.
Settings -> Pages -> Source: GitHub Actions.
(Opcional) Settings -> Secrets -> UEX_TOKEN si UEX te da un token.
Pestana Actions -> "Actualizar datos y publicar" -> Run workflow.
Abre https://TU_USUARIO.github.io/TU_REPO/data/meta.json.
Antes de fiarte de los datos

Ejecuta una vez en local: python collector/collect.py --inspect y comprueba que los campos de FIELDS (arriba en collect.py) coinciden, sobre todo stock_buy y demand_sell. meta.json indica en fields_available si se encontraron.

Si algo falla

El script aborta sin publicar y la web sigue con la ultima version buena.
