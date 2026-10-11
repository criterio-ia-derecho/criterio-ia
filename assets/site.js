(() => {
  'use strict';
  const $ = (selector, parent = document) => parent.querySelector(selector);
  const $$ = (selector, parent = document) => Array.from(parent.querySelectorAll(selector));
  const offline = document.documentElement.dataset.mode === 'offline';
  $('.skip-link').addEventListener('click', event => {
    event.preventDefault();
    const visibleMain = $$('main').find(main => !main.hidden);
    const heading = visibleMain && $('h1', visibleMain);
    if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus(); heading.scrollIntoView(); }
  });
  const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const search = $('#case-search');
  const topic = $('#topic-select');
  const month = $('#month-select');
  const sort = $('#sort-select');
  const clearFilters = $('#clear-filters');
  const monthGroups = $$('.archive-month');
  const cards = $$('.case-card');
  let country = 'all';
  let toastTimer;
  const toast = message => {
    const element = $('#toast');
    element.textContent = message;
    element.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { element.hidden = true; }, 4200);
  };
  const applyFilters = () => {
    if (!search) return;
    // Older cached pages do not yet include the publication controls.
    const selectedMonth = month ? month.value : '';
    const query = normalize(search.value);
    const terms = query.split(/\s+/).filter(Boolean);
    let count = 0;
    cards.forEach(card => {
      const text = normalize(card.dataset.search || card.textContent);
      const show = (country === 'all' || card.dataset.country === country) &&
        (!topic.value || card.dataset.topic === topic.value) &&
        (!selectedMonth || (card.dataset.published || '').startsWith(selectedMonth)) && terms.every(term => text.includes(term));
      card.hidden = !show;
      count += Number(show);
    });
    const direction = sort && sort.value === 'asc' ? 1 : -1;
    // ISO dates sort chronologically; ties retain the editorial order.
    monthGroups.slice().sort((a, b) => direction * a.dataset.month.localeCompare(b.dataset.month)).forEach(group => {
      const items = $$('.case-card', group);
      const visible = items.filter(card => !card.hidden).length;
      group.hidden = visible === 0;
      $('.month-count', group).textContent = visible === 1 ? '1 nota' : `${visible} notas`;
      items.sort((a, b) => direction * a.dataset.published.localeCompare(b.dataset.published) || Number(a.dataset.order) - Number(b.dataset.order)).forEach(card => $('.article-grid', group).append(card));
      $('#archive-months').append(group);
    });
    $('#results-count').textContent = count === 1 ? '1 nota disponible' : `${count} notas disponibles`;
    if (clearFilters) clearFilters.hidden = !(search.value || topic.value || selectedMonth || country !== 'all');
    $('#empty-results').hidden = count !== 0;
    $('#clear-search').hidden = !search.value;
  };
  if (search) {
    $$('.search-tools').forEach(item => { item.hidden = false; });
    search.addEventListener('input', applyFilters);
    topic.addEventListener('change', applyFilters);
    if (month) month.addEventListener('change', applyFilters);
    if (sort) sort.addEventListener('change', applyFilters);
    window.addEventListener('pageshow', applyFilters);
    $$('.filter-button').forEach(button => button.addEventListener('click', () => {
      country = button.dataset.country;
      $$('.filter-button').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      applyFilters();
    }));
    $('#clear-search').addEventListener('click', () => { search.value = ''; applyFilters(); search.focus(); });
    const resetFilters = () => {
      search.value = ''; topic.value = ''; country = 'all';
      if (month) month.value = '';
      $$('.filter-button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.country === 'all')));
      applyFilters(); search.focus();
    };
    $('#reset-filters').addEventListener('click', resetFilters);
    if (clearFilters) clearFilters.addEventListener('click', resetFilters);
    applyFilters();
  }
  const showFallback = (button, value, message) => {
    const container = button.closest('.article-body').querySelector('.citation-fallback');
    container.hidden = false;
    container.querySelector('label').textContent = message;
    const field = container.querySelector('textarea');
    field.value = value;
    field.focus();
    field.select();
    toast('Selecciona el texto para copiarlo.');
  };
  const copy = async (button, value, success, fallback) => {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(value); toast(success); return; } catch (_) { /* Manual fallback below. */ }
    }
    showFallback(button, value, fallback);
  };
  $$('.copy-citation').forEach(button => button.addEventListener('click', () => {
    copy(button, button.dataset.citation, 'Cita del comentario copiada.', 'Cita del comentario: selecciona y copia este texto.');
  }));
  $$('.share-source').forEach(button => button.addEventListener('click', async () => {
    const details = { title: button.dataset.title, text: 'Fuente oficial del caso comentado en Criterio IA.', url: button.dataset.source };
    if (navigator.share) {
      try { await navigator.share(details); return; } catch (error) { if (error.name === 'AbortError') return; }
    }
    copy(button, button.dataset.source, 'Enlace a la fuente copiado.', 'Fuente oficial: selecciona y copia este enlace para compartirlo.');
  }));
  $$('.print-article').forEach(button => button.addEventListener('click', () => window.print()));
  if (offline) {
    const home = $('#home-view');
    const views = $$('.article-view');
    const route = () => {
      const hash = decodeURIComponent(location.hash.slice(1));
      const slug = hash.startsWith('articulo/') ? hash.slice('articulo/'.length).split('/')[0] : '';
      const article = views.find(view => view.dataset.slug === slug);
      home.hidden = Boolean(article);
      views.forEach(view => { view.hidden = view !== article; });
      document.title = article ? `${article.dataset.title} · Criterio IA` : 'Criterio IA · Derecho e inteligencia artificial';
      document.body.classList.toggle('reading', Boolean(article));
      if (article) {
        window.scrollTo({ top: 0, behavior: 'instant' });
        article.querySelector('h1').focus({ preventScroll: true });
      } else {
        requestAnimationFrame(() => {
          const anchor = document.getElementById(hash);
          if (anchor && home.contains(anchor)) anchor.scrollIntoView({ behavior: 'instant' });
          else window.scrollTo({ top: 0, behavior: 'instant' });
        });
      }
      updateProgress();
    };
    $$('[data-article]').forEach(link => link.addEventListener('click', event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const target = `#articulo/${link.dataset.article}`;
      if (location.hash === target) route(); else location.hash = target;
    }));
    $$('[data-home]').forEach(link => link.addEventListener('click', event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const target = link.dataset.home || 'inicio';
      if (location.hash === `#${target}`) route(); else location.hash = target;
    }));
    $$('[data-scroll]').forEach(link => link.addEventListener('click', event => {
      event.preventDefault();
      const target = document.getElementById(link.dataset.scroll);
      if (target) target.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    }));
    window.addEventListener('hashchange', route);
    window.addEventListener('pageshow', route);
    route();
  }
  function updateProgress() {
    const progress = $('#reading-progress');
    if (!progress) return;
    const article = $$('.article-view').find(view => !view.hidden) || $('main.article-page');
    if (!article) { progress.style.width = '0%'; return; }
    const body = $('.article-body', article);
    if (!body) return;
    const rect = body.getBoundingClientRect();
    const distance = Math.max(1, body.offsetHeight - window.innerHeight * .55);
    const percent = Math.min(100, Math.max(0, (window.innerHeight * .15 - rect.top) / distance * 100));
    progress.style.width = `${percent}%`;
  }
  window.addEventListener('scroll', updateProgress, { passive: true });
  window.addEventListener('resize', updateProgress);
  updateProgress();
})();
