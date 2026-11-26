// Menú común de todas las páginas: para añadir una sección nueva solo hay que tocar esta lista.
(function () {
  var L = [['index.html', './', 'Inicio'], ['comercio.html', 'comercio.html', 'Comercio'], ['naves.html', 'naves.html', 'Naves'],
           ['blueprints.html', 'blueprints.html', 'Blueprints'], ['misiones.html', 'misiones.html', 'Misiones'],
           ['mineria.html', 'mineria.html', 'Minería'], ['eventos.html', 'eventos.html', 'Eventos']];
  function run() {
    var el = document.getElementById('nav');
    if (!el) return;
    var f = location.pathname.split('/').pop() || 'index.html';
    if (!document.getElementById('nav-css')) {      // en el móvil el menú es una sola fila que se desliza
      var st = document.createElement('style'); st.id = 'nav-css';
      st.textContent = '@media(max-width:760px){#nav{display:flex!important;gap:8px;overflow-x:auto;flex:1 1 100%;order:5;padding-bottom:6px}#nav .nl{white-space:nowrap;flex:0 0 auto}.top .info{order:4}}';
      document.head.appendChild(st);
    }
    el.innerHTML = L.map(function (x) { return '<a class="nl' + (x[0] === f ? ' on' : '') + '" href="' + x[1] + '">' + x[2] + '</a>'; }).join('');
    var on = el.querySelector('.on');
    if (on && on.scrollIntoView) on.scrollIntoView({inline: 'center', block: 'nearest'});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run();
})();
