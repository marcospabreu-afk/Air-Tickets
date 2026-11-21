/* Sugestões de aeroportos (compartilhado pelas páginas Buscar e Monitorar).
   ligarSugestoes(entrada, lista, { regioes: bool, aoEscolher: function(item) }) */
function ligarSugestoes(entrada, lista, opcoes) {
  var itens = [], ativo = -1, espera = null, ultimo = "";
  function fechar() { lista.hidden = true; entrada.setAttribute("aria-expanded", "false"); ativo = -1; }
  function escolher(a) { fechar(); opcoes.aoEscolher(a); }
  function marcar(i) {
    ativo = i;
    Array.prototype.forEach.call(lista.children, function (li, j) {
      li.setAttribute("aria-selected", j === i ? "true" : "false");
      if (j === i) li.scrollIntoView({ block: "nearest" });
    });
  }
  function mostrar(resultado) {
    itens = resultado; lista.innerHTML = "";
    resultado.forEach(function (a) {
      var li = document.createElement("li");
      li.setAttribute("role", "option");
      if (a.regiao) li.className = "regiao";
      var cod = document.createElement("b"); cod.textContent = a.regiao ? "◎" : a.iata;
      var txt = document.createElement("span");
      txt.textContent = a.regiao ? a.nome : (a.cidade ? a.cidade + " – " : "") + a.nome;
      var pais = document.createElement("em"); pais.textContent = a.pais;
      li.appendChild(cod); li.appendChild(txt); li.appendChild(pais);
      li.addEventListener("mousedown", function (e) { e.preventDefault(); escolher(a); });
      lista.appendChild(li);
    });
    lista.hidden = resultado.length === 0;
    entrada.setAttribute("aria-expanded", resultado.length ? "true" : "false");
    ativo = -1;
  }
  entrada.addEventListener("input", function () {
    if (opcoes.aoDigitar) opcoes.aoDigitar();
    var q = entrada.value.trim();
    clearTimeout(espera);
    if (q.length < 2) { fechar(); return; }
    espera = setTimeout(function () {
      ultimo = q;
      fetch("/api/aeroportos?q=" + encodeURIComponent(q) + (opcoes.regioes ? "&regioes=1" : ""),
            { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (dados) { if (q === ultimo) mostrar(dados); })
        .catch(fechar);
    }, 150);
  });
  entrada.addEventListener("keydown", function (e) {
    if (lista.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); marcar(Math.min(ativo + 1, itens.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); marcar(Math.max(ativo - 1, 0)); }
    else if (e.key === "Enter" && itens.length) { e.preventDefault(); escolher(itens[ativo < 0 ? 0 : ativo]); }
    else if (e.key === "Escape") { fechar(); }
  });
  entrada.addEventListener("blur", function () { setTimeout(fechar, 100); });
}

/* Campo de um aeroporto só: o valor vira o código e o nome aparece embaixo. */
function campoAeroporto(id) {
  var entrada = document.getElementById(id);
  if (!entrada) return;
  var rotulo = document.getElementById("rotulo-" + id);
  entrada.addEventListener("focus", function () { entrada.select(); });
  ligarSugestoes(entrada, document.getElementById("sugestoes-" + id), {
    aoDigitar: function () { rotulo.textContent = ""; },
    aoEscolher: function (a) { entrada.value = a.iata; rotulo.textContent = a.rotulo; }
  });
}
