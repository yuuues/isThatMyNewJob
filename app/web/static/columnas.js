/* Columnas del listado redimensionables arrastrando el borde de la cabecera.
 *
 * El reparto por defecto vive en estilo.css; esto sólo lo sobreescribe cuando el
 * usuario arrastra, y lo recuerda en este navegador. Sin JavaScript, o con el
 * almacenamiento bloqueado, se queda el reparto por defecto: nada deja de funcionar.
 *
 * Se arrastra el borde derecho de una columna y el ancho se lo cede o se lo quita a
 * la de al lado, así la tabla nunca se sale de la pantalla. Todas las tablas del
 * listado (una por grupo) comparten los anchos para que las columnas sigan alineadas
 * de un grupo al siguiente. Doble clic en un borde vuelve al reparto por defecto.
 *
 * Los anchos se guardan en porcentaje y no en píxeles: así valen igual en el portátil
 * que en el monitor grande.
 */
(function () {
  var CLAVE = "anchos-columnas-lista";
  var MINIMO_PX = 48;

  function leer() {
    try {
      var valor = JSON.parse(localStorage.getItem(CLAVE));
      return Array.isArray(valor) ? valor : null;
    } catch (e) {
      return null;
    }
  }

  function guardar(anchos) {
    try {
      if (anchos) {
        localStorage.setItem(CLAVE, JSON.stringify(anchos));
      } else {
        localStorage.removeItem(CLAVE);
      }
    } catch (e) {
      /* Sin almacenamiento el ajuste vale para esta página y no se recuerda. */
    }
  }

  function tablas() {
    return document.querySelectorAll("#lista table");
  }

  function aplicar(anchos) {
    tablas().forEach(function (tabla) {
      tabla.querySelectorAll("thead th").forEach(function (th, i) {
        th.style.width = anchos && anchos[i] != null ? anchos[i] + "%" : "";
      });
    });
  }

  function empiezaArrastre(evento, tabla, indice) {
    evento.preventDefault();
    var cabeceras = tabla.querySelectorAll("thead th");
    var total = tabla.offsetWidth;
    var anchos = Array.prototype.map.call(cabeceras, function (th) {
      return th.offsetWidth;
    });
    var inicioX = evento.clientX;
    var par = anchos[indice] + anchos[indice + 1];
    var tirador = evento.currentTarget;
    tirador.setPointerCapture(evento.pointerId);

    function mueve(e) {
      var propio = Math.min(
        Math.max(anchos[indice] + e.clientX - inicioX, MINIMO_PX),
        par - MINIMO_PX
      );
      var nuevos = anchos.slice();
      nuevos[indice] = propio;
      nuevos[indice + 1] = par - propio;
      aplicar(
        nuevos.map(function (px) {
          return Math.round((px / total) * 1000) / 10;
        })
      );
    }

    function suelta() {
      tirador.removeEventListener("pointermove", mueve);
      tirador.removeEventListener("pointerup", suelta);
      tirador.removeEventListener("pointercancel", suelta);
      guardar(
        Array.prototype.map.call(tabla.querySelectorAll("thead th"), function (th) {
          return Math.round((th.offsetWidth / total) * 1000) / 10;
        })
      );
    }

    tirador.addEventListener("pointermove", mueve);
    tirador.addEventListener("pointerup", suelta);
    tirador.addEventListener("pointercancel", suelta);
  }

  function preparar() {
    tablas().forEach(function (tabla) {
      var cabeceras = tabla.querySelectorAll("thead th");
      // La última columna no lleva tirador: no tiene vecina a la derecha a la que
      // ceder el ancho.
      for (var i = 0; i < cabeceras.length - 1; i++) {
        if (cabeceras[i].querySelector("[data-tirador]")) continue;
        var tirador = document.createElement("span");
        tirador.dataset.tirador = "";
        tirador.title = "Arrastra para cambiar el ancho · doble clic para restaurar";
        tirador.addEventListener(
          "pointerdown",
          (function (indice) {
            return function (e) {
              empiezaArrastre(e, tabla, indice);
            };
          })(i)
        );
        tirador.addEventListener("dblclick", function () {
          guardar(null);
          aplicar(null);
        });
        cabeceras[i].appendChild(tirador);
      }
    });
    aplicar(leer());
  }

  preparar();
  // Al filtrar, HTMX sustituye `#lista` entera: las tablas nuevas llegan sin
  // tiradores y con el reparto por defecto.
  document.body.addEventListener("htmx:afterSwap", preparar);
})();
