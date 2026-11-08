(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const byId = (id) => document.getElementById(id);
  const API = '/api';
  const state = { data: null, watchById: new Map(), loading: false, formBusy: false, settingsBusy: false, notificationConfigBusy: false, notificationFeedback: '', search: '', expandedWatches: new Set(), sourceIntervalDraft: {}, bfDurationDraft: 86400 };
  const dialog = byId('watch-dialog');
  const resolveDialog = byId('resolve-dialog');

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function svgEl(tag, attrs = {}) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
  }
  function fmtMoney(cents, currency = 'EUR') {
    if (!Number.isInteger(cents) || cents < 0) return 'Unbekannt';
    try { return new Intl.NumberFormat('de-DE', { style: 'currency', currency }).format(cents / 100); }
    catch { return `${(cents / 100).toFixed(2).replace('.', ',')} €`; }
  }
  function fmtTime(epoch, options = {}) {
    if (typeof epoch !== 'number' || !Number.isFinite(epoch) || epoch <= 0) return 'Noch keine Prüfung';
    return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short', ...options }).format(new Date(epoch * 1000));
  }
  function ageText(epoch) {
    if (!Number.isFinite(epoch) || epoch <= 0) return 'Noch nie geprüft';
    const mins = Math.max(0, Math.floor((Date.now() / 1000 - epoch) / 60));
    if (mins < 1) return 'Gerade eben geprüft';
    if (mins < 60) return `Vor ${mins} Min. geprüft`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `Vor ${hours} Std. geprüft`;
    return `Vor ${Math.floor(hours / 24)} Tagen geprüft`;
  }
  function splitList(text, maximum = 20) {
    return text.split(/[\n,;]/).map((part) => part.trim()).filter(Boolean).slice(0, maximum);
  }
  function moneyInputToCents(value, label) {
    const raw = value.trim().replace(/\s|€/g, '');
    if (!raw) return null;
    if (!/^\d{1,9}(?:[,.]\d{1,2})?$/.test(raw)) throw new Error(`${label}: Bitte einen positiven EUR-Betrag mit höchstens zwei Nachkommastellen eingeben.`);
    const normalized = raw.replace(',', '.');
    const cents = Math.round(Number(normalized) * 100);
    if (!Number.isSafeInteger(cents)) throw new Error(`${label}: Betrag ist zu groß.`);
    return cents;
  }
  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');
    const response = await fetch(`${API}${path}`, { ...options, headers, body: options.body === undefined ? undefined : JSON.stringify(options.body), cache: 'no-store' });
    const text = await response.text();
    let payload = null;
    if (text) {
      try { payload = JSON.parse(text); }
      catch { throw new Error(`Antwort des lokalen Dienstes ist ungültig (HTTP ${response.status}).`); }
    }
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      if (typeof payload?.detail === 'string') detail = payload.detail;
      else if (Array.isArray(payload?.detail)) {
        const messages = payload.detail.map(item => typeof item?.msg === 'string' ? item.msg : '').filter(Boolean);
        if (messages.length) detail = messages.join(' · ');
      }
      throw new Error(detail);
    }
    return payload;
  }
  function toast(message, type = '') {
    const item = el('div', `toast ${type}`, message);
    item.setAttribute('role', 'status');
    byId('toast-region').append(item);
    window.setTimeout(() => item.remove(), 4200);
  }
  function setHealth(ok, label = '') {
    const wrap = byId('side-health');
    const dot = $('.health-dot', wrap);
    dot.classList.toggle('ok', ok);
    dot.classList.toggle('bad', !ok);
    $('strong', wrap).textContent = label || (ok ? 'Dienst verbunden' : 'Dienst nicht erreichbar');
    $('small', wrap).textContent = 'Lokaler Dienst';
  }
  function safeHttpUrl(raw) {
    if (typeof raw !== 'string' || !raw.trim()) return null;
    try {
      const parsed = new URL(raw);
      if ((parsed.protocol === 'https:' || parsed.protocol === 'http:') && parsed.hostname && !parsed.username && !parsed.password) return parsed.href;
    } catch { /* invalid product URL: render as unavailable */ }
    return null;
  }
  function safeHttpsUrl(raw) {
    const safe = safeHttpUrl(raw);
    if (!safe) return null;
    try { return new URL(safe).protocol === 'https:' ? safe : null; }
    catch { return null; }
  }
  function offerState(offer) {
    if (isReferenceOffer(offer)) return ['warn', 'Referenzpreis · kein Händlerangebot'];
    const evaluation = String(offer?.evaluation || 'Unzureichende Daten');
    if (/Wunschpreis|Bestpreis|Sehr gutes|Preisrückgang/i.test(evaluation)) return ['good', evaluation];
    if (/Unzureichend|Kein relevantes/i.test(evaluation)) return ['warn', evaluation];
    return ['', evaluation];
  }
  function isReferenceOffer(offer) {
    return offer?.provenance === 'reference' || offer?.source === 'bestenvergleich' || offer?.offer_type === 'reference';
  }
  function conditionLabel(value) {
    return ({ new: 'Neu', used: 'Gebraucht', refurbished: 'Generalüberholt', unknown: 'Unbekannt' })[value] || '';
  }
  function offerTypeLabel(value) {
    return ({ retail: 'Händlerangebot', reference: 'Referenzpreis' })[value] || '';
  }
  function renderStats(data) {
    const watches = Array.isArray(data.watches) ? data.watches : [];
    const offers = Array.isArray(data.offers) ? data.offers : [];
    const events = Array.isArray(data.events) ? data.events : [];
    const sources = Array.isArray(data.sources) ? data.sources : [];
    byId('stat-watches').textContent = watches.filter(w => !w.paused).length;
    byId('stat-watches-foot').textContent = `${watches.filter(w => w.paused).length} pausiert · ${watches.length} insgesamt`;
    byId('stat-offers').textContent = offers.length;
    byId('stat-events').textContent = events.length;
    byId('stat-sources').textContent = sources.filter(s => s.configured !== false).length;
    byId('nav-watch-count').textContent = watches.length;
  }
  function renderWatchList(data) {
    const list = byId('watch-list');
    list.replaceChildren();
    const watches = Array.isArray(data.watches) ? data.watches : [];
    const offersByWatch = new Map();
    for (const offer of (Array.isArray(data.offers) ? data.offers : [])) {
      if (!offersByWatch.has(String(offer.watch_id))) offersByWatch.set(String(offer.watch_id), []);
      offersByWatch.get(String(offer.watch_id)).push(offer);
    }
    const query = state.search.toLocaleLowerCase('de-DE');
    const visible = watches.filter(w => !query || `${w.name || ''} ${w.query || ''} ${w.manufacturer || ''}`.toLocaleLowerCase('de-DE').includes(query));
    byId('watch-list-count').textContent = `${visible.length}`;
    if (!watches.length) {
      const empty = el('div', 'empty-state');
      empty.append(el('div', 'empty-icon', '◎'), el('strong', '', 'Noch keine Beobachtungen'), el('p', '', 'Lege ein Produkt an, um echte Quellpreise und deren zeitlichen Verlauf lokal zu verfolgen.'));
      const button = el('button', 'button button-primary', '＋ Beobachtung anlegen'); button.type = 'button'; button.addEventListener('click', () => openWatchEditor()); empty.append(button); list.append(empty); return;
    }
    if (!visible.length) { list.append(el('div', 'empty-state', 'Keine Beobachtung passt zu deiner Suche.')); return; }
    visible.forEach((watch, index) => {
      const card = el('article', 'watch-card');
      card.dataset.watchId = String(watch.id);
      const main = el('div', 'watch-main');
      const product = el('div', 'product-title');
      const monogram = el('div', `product-monogram alt-${index % 4}`, String(watch.name || '?').trim().charAt(0).toLocaleUpperCase('de-DE') || '?'); monogram.setAttribute('aria-hidden', 'true');
      const copy = el('div', 'product-copy');
      copy.append(el('strong', '', watch.name || 'Ohne Namen'));
      const sourceNames = (watch.source_ids || []).map(id => (data.sources || []).find(s => s.id === id)?.name || id).join(', ');
      const watchOffers = (offersByWatch.get(String(watch.id)) || []).slice().sort((a, b) => {
        const staleOrder = Number(!!a.stale) - Number(!!b.stale);
        if (staleOrder) return staleOrder;
        const availabilityOrder = Number(a.availability !== 'in_stock') - Number(b.availability !== 'in_stock');
        if (availabilityOrder) return availabilityOrder;
        const aTotal = Number.isInteger(a.total_cents) ? a.total_cents : Number.POSITIVE_INFINITY;
        const bTotal = Number.isInteger(b.total_cents) ? b.total_cents : Number.POSITIVE_INFINITY;
        return aTotal - bTotal || (Number(b.checked_at) || 0) - (Number(a.checked_at) || 0);
      });
      const offer = watchOffers[0] || null;
      const reference = isReferenceOffer(offer);
      let productSubline = `${watch.kind === 'discovery' ? 'Entdecken' : 'Exakt'}${watch.query ? ` · ${watch.query}` : ''}${sourceNames ? ` · ${sourceNames}` : ''}`;
      if (offer) {
        const headline = offer.stale ? 'Letzter bekannter Treffer: ' : watchOffers.length > 1 && offer.total_cents != null ? 'Günstigster Treffer: ' : watchOffers.length > 1 ? 'Treffer: ' : '';
        productSubline += ` · ${headline}${offer.name || watch.name}`;
      }
      copy.append(el('small', '', productSubline));
      product.append(monogram, copy);

      const priceBox = el('div');
      if (offer) {
        if (reference) {
          priceBox.append(el('span', 'current-price reference-price', `Referenzpreis ${fmtMoney(offer.price_cents, offer.currency)}`));
          priceBox.append(el('small', 'price-detail', 'Versand/Händler unbekannt · kein Gesamtpreisalarm'));
        } else {
        const totalLabel = offer.total_cents == null ? 'Gesamt unbekannt' : fmtMoney(offer.total_cents, offer.currency);
        const headlinePrice = offer.stale ? `Veraltet · ${totalLabel}` : watchOffers.length > 1 && offer.total_cents != null ? `Ab ${totalLabel}` : totalLabel;
        priceBox.append(el('span', 'current-price', headlinePrice));
        let detail = `${fmtMoney(offer.price_cents, offer.currency)} Artikel`;
        detail += offer.shipping_cents == null ? ' · Versand unbekannt' : offer.shipping_cents === 0 ? ' · Versand inklusive' : ` · + ${fmtMoney(offer.shipping_cents, offer.currency)} Versand`;
        if (Number.isInteger(offer.comparison_cents) && offer.comparison_cents >= 0) {
          detail += ` · beobachteter Vergleich ${fmtMoney(offer.comparison_cents, offer.currency)}`;
          if (Number.isInteger(offer.savings_cents) && offer.savings_cents > 0) detail += ` · ${fmtMoney(offer.savings_cents, offer.currency)} darunter`;
        }
        const details = el('small', 'price-detail', detail);
        priceBox.append(details);
        }
      } else {
        priceBox.append(el('span', 'current-price', 'Noch kein Angebot'), el('small', 'price-detail', watch.last_error ? 'Letzte Prüfung mit Fehler' : 'Wird nach erster Prüfung angezeigt'));
      }
      const status = el('span', `offer-state ${offer ? offerState(offer)[0] : 'warn'}`, offer ? offerState(offer)[1] : (watch.paused ? 'Pausiert' : 'Noch nicht geprüft'));
      const actions = el('div', 'watch-actions');
      const check = el('button', 'small-action', 'Prüfen'); check.type = 'button'; check.setAttribute('aria-label', `${watch.name}: Jetzt prüfen`); check.disabled = !!watch.running || !!watch.paused || !!data.settings?.paused; check.title = check.disabled ? (watch.paused || data.settings?.paused ? 'Prüfungen sind pausiert' : 'Prüfung läuft bereits') : 'Jetzt manuell prüfen'; check.addEventListener('click', () => manualCheck(watch, check));
      const historyButton = el('button', 'small-action', 'Verlauf'); historyButton.type = 'button'; historyButton.setAttribute('aria-label', `${watch.name}: Preisverlauf anzeigen`); historyButton.disabled = !offer; historyButton.addEventListener('click', () => showHistory(offer, watch));
      const edit = el('button', 'small-action', 'Bearbeiten'); edit.type = 'button'; edit.addEventListener('click', () => openWatchEditor(watch));
      const remove = el('button', 'small-action', 'Entfernen'); remove.type = 'button'; remove.addEventListener('click', () => removeWatch(watch));
      actions.append(check, historyButton, edit, remove);
      main.append(product, priceBox, status, actions);

      const meta = el('div', 'watch-meta');
      const left = el('div', 'meta-left');
      const lastChecked = Number.isFinite(watch.last_checked) ? watch.last_checked : (offer?.checked_at ?? null);
      const age = el('span', `meta-item${offer?.stale ? ' stale' : ''}`);
      age.append(el('span', '', offer?.stale ? '⚠' : '◷'), el('strong', '', offer?.stale ? `Veraltet · ${ageText(lastChecked)}` : ageText(lastChecked)));
      left.append(age);
      if (watch.last_error) left.append(el('span', 'meta-item stale', `Letzter Fehler: ${watch.last_error}`));
      if (watch.target_cents != null) left.append(el('span', 'meta-item', `Wunschpreis ${fmtMoney(watch.target_cents)}`));
      if (watch.max_price_cents != null) left.append(el('span', 'meta-item', `Limit ${fmtMoney(watch.max_price_cents)}`));
      if (watch.reference_max_price_cents != null) left.append(el('span', 'meta-item', `Referenzbudget ${fmtMoney(watch.reference_max_price_cents)} · Artikelpreis`));
      const right = el('div', 'meta-right');
      if (offer) {
        right.append(el('span', 'meta-item', offer.merchant ? `Quelle: ${offer.merchant}` : `Quelle: ${offer.source || 'Unbekannt'}`));
        if (offer.availability) right.append(el('span', 'meta-item', availabilityLabel(offer.availability, offer.provenance)));
      }
      if (watch.paused) right.append(el('span', 'meta-item', 'Pausiert'));
      const effective = watch.effective_source_intervals || {};
      for (const [sourceId, intervals] of Object.entries(effective)) {
        if (!intervals || (!intervals.normal_seconds && !intervals.black_friday_seconds)) continue;
        const normal = intervals.normal_seconds ? `${Math.round(intervals.normal_seconds / 60)} Min.` : '–';
        const bf = intervals.black_friday_seconds ? `${Math.round(intervals.black_friday_seconds / 60)} Min.` : '–';
        right.append(el('span', 'meta-item', `${sourceId}: ${normal} normal · ${bf} BF`));
      }
      meta.append(left, right);
      card.append(main, meta);
      if (watchOffers.length) {
        const disclosure = document.createElement('details');
        disclosure.className = 'watch-offers';
        disclosure.open = state.expandedWatches.has(String(watch.id));
        disclosure.addEventListener('toggle', () => {
          if (disclosure.open) state.expandedWatches.add(String(watch.id));
          else state.expandedWatches.delete(String(watch.id));
        });
        const summary = el('summary', 'offers-summary', `${watchOffers.length} Angebot${watchOffers.length === 1 ? '' : 'e'} anzeigen`);
        summary.setAttribute('aria-label', `${watchOffers.length} einzelne Angebote für ${watch.name} anzeigen oder ausblenden`);
        const offerList = el('div', 'individual-offers');
        watchOffers.forEach(item => offerList.append(renderOffer(item, watch)));
        disclosure.append(summary, offerList);
        card.append(disclosure);
      }
      const comparisons = Array.isArray(data.comparisons) ? data.comparisons : [];
      const ids = new Set(watchOffers.map(item => Number(item.id)));
      const matching = comparisons.filter(group => {
        if (!Array.isArray(group.offer_ids)) return false;
        if (!group.offer_ids.some(id => ids.has(Number(id)))) return false;
        const matched = (Array.isArray(data.offers) ? data.offers : []).filter(item => group.offer_ids.some(id => Number(id) === Number(item.id)));
        return new Set(matched.map(item => item.merchant || item.seller || item.source)).size > 1;
      });
      if (matching.length) {
        const compare = el('section', 'comparison-block');
        compare.append(el('strong', '', 'Quellenübergreifender Produktvergleich'));
        for (const group of matching) {
          const matched = (Array.isArray(data.offers) ? data.offers : []).filter(item => group.offer_ids.some(id => Number(id) === Number(item.id)));
          const groupNode = el('div', 'comparison-group');
          groupNode.append(el('small', '', `${group.identity_basis === 'gtin' ? 'GTIN' : 'Hersteller + Modell'} ${group.identity_value} · niedrigster Gesamtpreis ${fmtMoney(group.lowest_total_cents)}`));
          matched.forEach(item => {
            const line = el('div', 'comparison-offer');
            const label = [item.name, item.seller || item.merchant || 'Händler unbekannt', item.pack_size, conditionLabel(item.condition)].filter(Boolean).join(' · ');
            line.append(el('span', '', label), el('strong', '', item.total_cents == null ? 'Gesamt unbekannt' : fmtMoney(item.total_cents)));
            line.append(el('small', '', ageText(item.checked_at)));
            const itemUrl = safeHttpUrl(item.url);
            if (itemUrl) { const link = el('a', '', 'Angebot ↗'); link.href = itemUrl; link.target = '_blank'; link.rel = 'noopener noreferrer'; line.append(link); }
            groupNode.append(line);
          });
          compare.append(groupNode);
        }
        card.append(compare);
      }
      list.append(card);
    });
  }
  function renderOffer(offer, watch) {
    const reference = isReferenceOffer(offer);
    const row = el('article', 'individual-offer');
    const heading = el('div', 'individual-offer-heading');
    const title = el('div', 'individual-offer-title');
    title.append(el('strong', '', offer.name || 'Unbenanntes Produkt'));
    title.append(el('small', '', reference ? 'Händler unbekannt' : offer.seller || offer.merchant || 'Händler unbekannt'));
    heading.append(title);
    const actions = el('div', 'individual-offer-actions');
    const exact = el('button', 'small-action', 'Exakt beobachten'); exact.type = 'button';
    exact.addEventListener('click', () => openWatchEditor(null, offer));
    actions.append(exact);
    const history = el('button', 'small-action', 'Verlauf');
    history.type = 'button';
    history.setAttribute('aria-label', `Preisverlauf für ${offer.name || 'dieses Angebot'} anzeigen`);
    history.addEventListener('click', () => showHistory(offer, watch));
    actions.append(history);
    const href = safeHttpUrl(offer.url);
    if (href) {
      const link = el('a', 'small-action offer-link', 'Angebot ↗');
      link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      actions.append(link);
    }
    heading.append(actions);
    const price = el('div', 'individual-offer-price');
    price.append(el('strong', reference ? 'offer-total reference-price' : 'offer-total', reference ? `Referenzpreis ${fmtMoney(offer.price_cents, offer.currency)}` : offer.total_cents == null ? 'Gesamt unbekannt' : fmtMoney(offer.total_cents, offer.currency)));
    if (!reference) {
      price.append(el('span', '', `${fmtMoney(offer.price_cents, offer.currency)} Artikel`));
      price.append(el('span', '', offer.shipping_cents == null ? 'Versand unbekannt' : `Versand ${fmtMoney(offer.shipping_cents, offer.currency)}`));
    } else price.append(el('span', 'reference-caveat', 'Versand/Händler unbekannt · kein Gesamtpreisalarm'));
    const chips = el('div', 'individual-offer-meta');
    chips.append(el('span', 'meta-chip', availabilityLabel(offer.availability, offer.provenance)));
    if (offer.seller) chips.append(el('span', 'meta-chip', `Verkäufer: ${offer.seller}`));
    if (offer.pack_size) chips.append(el('span', 'meta-chip', `Packung: ${offer.pack_size}`));
    if (conditionLabel(offer.condition)) chips.append(el('span', 'meta-chip', `Zustand: ${conditionLabel(offer.condition)}`));
    if (offerTypeLabel(offer.offer_type)) chips.append(el('span', 'meta-chip', `Angebotstyp: ${offerTypeLabel(offer.offer_type)}`));
    chips.append(el('span', 'meta-chip', `Quelle: ${offer.source || 'unbekannt'}`));
    const fromFeed = offer.provenance === 'feed' || offer.quality === 'feed_reported';
    chips.append(el('span', `meta-chip${offer.stale ? ' stale' : ''}`, `${offer.stale ? 'Veraltet · ' : ''}${fromFeed ? 'Katalogdaten · ' : ''}${ageText(offer.checked_at)}`));
    chips.append(el('span', 'meta-chip', `${fromFeed ? 'Feed-Zeitpunkt' : 'Geprüft'}: ${fmtTime(offer.checked_at)}`));
    if (Number.isInteger(offer.comparison_cents)) chips.append(el('span', 'meta-chip', `Beobachteter Vergleich ${fmtMoney(offer.comparison_cents, offer.currency)}`));
    if (Number.isInteger(offer.savings_cents)) chips.append(el('span', 'meta-chip', `Ersparnis ${fmtMoney(offer.savings_cents, offer.currency)}`));
    if (offer.coupon) chips.append(el('span', 'meta-chip offer-extra', `Gutschein: ${offer.coupon}`));
    if (offer.unit_price) chips.append(el('span', 'meta-chip offer-extra', `Grundpreis: ${offer.unit_price}`));
    if (reference) chips.append(el('span', 'meta-chip reference-chip', 'Evaluation: Referenzpreis'));
    row.append(heading, price, chips);
    return row;
  }
  function renderActivity(data) {
    const eventsNode = byId('events-list'); eventsNode.replaceChildren();
    const events = Array.isArray(data.events) ? data.events.slice(0, 8) : [];
    if (!events.length) eventsNode.append(el('div', 'list-empty', 'Noch keine relevanten Ereignisse beobachtet.'));
    for (const event of events) {
      const row = el('div', 'activity-row');
      const name = event.name || event.product || 'Preisereignis';
      row.append(el('strong', '', name));
      row.append(el('span', 'activity-value', event.total_cents == null ? 'Gesamt unbekannt' : fmtMoney(event.total_cents)));
      let eventDetail = `${event.reason || 'Beobachtet'} · ${fmtTime(event.checked_at || event.created_at)}`;
      if (event.notification_error) eventDetail += ` · ${event.notification_status === 'sent' ? 'Letzter Benachrichtigungsfehler' : 'Benachrichtigung: erneuter Versuch ausstehend'}${event.notification_attempts ? ` nach ${event.notification_attempts} Versuchen` : ''}: ${event.notification_error}`;
      else if (event.notification_status === 'pending') eventDetail += ' · Benachrichtigung ausstehend';
      else if (event.notification_status === 'sent') eventDetail += ' · Benachrichtigung gesendet';
      else if (event.notification_status === 'disabled') eventDetail += ' · Benachrichtigungen deaktiviert';
      row.append(el('small', '', eventDetail));
      const link = safeHttpUrl(event.url);
      if (link) { const anchor = el('a', 'activity-status', 'Angebot öffnen ↗'); anchor.href = link; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; row.append(anchor); }
      else row.append(el('span', 'activity-status', event.merchant || 'Lokal gespeichert'));
      eventsNode.append(row);
    }
    const checksNode = byId('checks-list'); checksNode.replaceChildren();
    const checks = Array.isArray(data.checks) ? data.checks.slice(0, 8) : [];
    if (!checks.length) checksNode.append(el('div', 'list-empty', 'Noch keine Quellenabfragen protokolliert.'));
    for (const check of checks) {
      const row = el('div', 'activity-row');
      row.append(el('strong', '', check.watch_name || check.name || `Beobachtung #${check.watch_id ?? '–'}`));
      const ok = check.status === 'ok';
      row.append(el('span', `activity-value${ok ? '' : ' bad-text'}`, ok ? `${Number(check.offers || 0)} Treffer` : (check.status || 'Fehler')));
      row.append(el('small', '', `${check.error || check.source_error || 'Quellenabfrage'} · ${fmtTime(check.checked_at || check.created_at || check.started_at)}`));
      row.append(el('span', 'activity-status', check.source || 'Lokaler Dienst'));
      checksNode.append(row);
    }
  }
  function renderSources(data) {
    const root = byId('sources-list'); root.replaceChildren();
    const sources = Array.isArray(data.sources) ? data.sources : [];
    if (!sources.length) { root.append(el('div', 'empty-state', 'Der Dienst meldet derzeit keine konfigurierten Quellen.')); return; }
    for (const source of sources) {
      const card = el('article', 'source-card'); const top = el('div', 'source-card-top');
      top.append(el('span', 'source-badge', String(source.name || source.id || '?').charAt(0).toLocaleUpperCase('de-DE')));
      const title = el('strong', '', source.name || source.id || 'Quelle'); top.append(title); card.append(top);
      card.append(el('p', '', source.description || 'Keine nähere Quellenbeschreibung verfügbar.'));
      const hasSuccess = Number.isFinite(source.last_success) && source.last_success > 0;
      const status = el('div', `source-status${source.last_error ? ' error' : ''}`);
      const dot = el('span', `health-dot${source.last_error ? ' bad' : hasSuccess ? ' ok' : ''}`); dot.setAttribute('aria-hidden', 'true'); status.append(dot);
      const statusText = source.configured === false ? 'Nicht konfiguriert' : source.last_error ? `Letzter Fehler: ${source.last_error}` : hasSuccess ? 'Quelle konfiguriert' : 'Quelle konfiguriert · noch nicht geprüft';
      status.append(document.createTextNode(statusText));
      const times = el('div', 'source-times');
      if (source.last_checked) times.append(el('span', '', `Letzte Prüfung: ${fmtTime(source.last_checked)}`));
      if (hasSuccess) times.append(el('span', '', `Letzte erfolgreiche Prüfung: ${fmtTime(source.last_success)}`));
      card.append(status, times);
      root.append(card);
    }
  }
  function renderSettings(data) {
    const settings = data.settings || {};
    byId('setting-paused').checked = !!settings.paused;
    const until = Number(settings.black_friday_until || 0);
    byId('setting-bf').checked = until > Date.now() / 1000;
    if (until > Date.now() / 1000) state.bfDurationDraft = until - Date.now() / 1000 > 86400 ? 604800 : 86400;
    byId('setting-bf-duration').value = String(state.bfDurationDraft);
    byId('setting-bf-duration').disabled = false;
    byId('bf-status').textContent = until > Date.now() / 1000 ? `Aktiv bis ${fmtTime(until)}.` : 'Der Modus ist nicht aktiv.';
    const configured = !!data.notification_configured;
    const configSource = data.notification_configuration_source || (configured ? 'environment' : 'none');
    byId('setting-notifications').checked = !!settings.notification_enabled;
    byId('setting-notifications').disabled = !configured;
    byId('notification-status').textContent = configured ? 'Kanal ist konfiguriert; lokale Ereignisse werden unabhängig davon gespeichert.' : 'Kein Benachrichtigungskanal konfiguriert. Der Schalter ist daher nicht verfügbar.';
    byId('notification-webhook').disabled = configSource === 'environment' || state.notificationConfigBusy;
    byId('notification-config-save').disabled = configSource === 'environment' || state.notificationConfigBusy;
    byId('notification-config-remove').disabled = configSource !== 'local' || state.notificationConfigBusy;
    byId('notification-config-help').textContent = configSource === 'environment'
      ? 'Die Webhook-Adresse stammt aus der Dienstumgebung und kann hier nicht bearbeitet werden.'
      : configSource === 'local'
        ? 'Eine lokale Adresse ist gespeichert, wird aber aus Sicherheitsgründen nie erneut angezeigt. Eine neue Eingabe ersetzt sie.'
        : 'Webhook-Adresse wird lokal gespeichert. Das Speichern sendet keine Testnachricht und aktiviert Benachrichtigungen nicht.';
    byId('settings-feedback').textContent = state.notificationFeedback;
  }
  function render(data) {
    state.data = data;
    state.watchById = new Map((data.watches || []).map(w => [String(w.id), w]));
    setHealth(true, 'Lokaler Dienst bereit');
    const sources = Array.isArray(data.sources) ? data.sources : [];
    const failedSources = sources.filter(source => source.last_error).length;
    const configuredSources = sources.filter(source => source.configured !== false).length;
    const coverage = byId('coverage-status');
    const coverageDot = $('.health-dot', coverage);
    const coverageText = !sources.length ? 'Keine Quellen gemeldet' : failedSources ? `${failedSources} ${failedSources === 1 ? 'Quelle mit' : 'Quellen mit'} Fehlerstatus` : `${configuredSources} von ${sources.length} Quellen konfiguriert`;
    coverageDot.className = `health-dot${failedSources ? ' bad' : sources.length && configuredSources === sources.length ? ' ok' : ''}`;
    coverage.lastChild.textContent = ` ${coverageText}`;
    renderStats(data); renderWatchList(data); renderActivity(data); renderSources(data); renderSettings(data);
    byId('updated-at').textContent = new Intl.DateTimeFormat('de-DE', { timeStyle: 'short' }).format(new Date());
  }
  async function refresh(showError = false) {
    if (state.loading) return;
    state.loading = true;
    try { render(await api('/state')); }
    catch (error) { setHealth(false); if (showError) toast(`Verbindung zum lokalen Dienst fehlgeschlagen: ${error.message}`, 'error'); }
    finally { state.loading = false; }
  }
  function clearForm() {
    byId('watch-form').reset();
    byId('watch-id').value = '';
    byId('watch-gtin').value = ''; byId('watch-model').value = ''; byId('watch-variant').value = '';
    byId('watch-category').value = ''; byId('watch-manufacturer').value = '';
    byId('watch-synonyms').value = ''; byId('watch-exclusions').value = '';
    byId('watch-merchants-include').value = ''; byId('watch-merchants-exclude').value = '';
    byId('watch-sellers-include').value = ''; byId('watch-sellers-exclude').value = '';
    byId('watch-bf-interval').value = '';
    state.sourceIntervalDraft = {};
    byId('watch-form-error').textContent = '';
    buildSourceOptions(state.data?.sources || []);
  }
  function buildSourceOptions(sources, selected = ['apple']) {
    const root = byId('source-options'); root.replaceChildren();
    const discovery = $('input[name="watch-kind"][value="discovery"]')?.checked;
    if (discovery) selected = selected.filter(id => sources.find(source => source.id === id)?.supports_search !== false);
    const available = [...sources];
    for (const id of selected) {
      if (!available.some(source => source.id === id)) available.push({ id, name: `${id} · nicht im Quellenkatalog`, configured: false, description: '' });
    }
    for (const source of available) {
      const label = el('label', 'source-check');
      const input = document.createElement('input'); input.type = 'checkbox'; input.name = 'watch-source'; input.value = source.id; input.checked = selected.includes(source.id); input.disabled = (source.configured === false && !selected.includes(source.id)) || (discovery && source.supports_search === false);
      input.addEventListener('change', () => renderSourceIntervalOptions());
      label.append(input, document.createTextNode(`${source.name || source.id}${source.supports_search === false ? ' · nur Link' : ''}`)); root.append(label);
    }
    if (!sources.length) root.append(el('span', 'field-help', 'Der Dienst meldet noch keine verfügbaren Quellen.'));
    renderSourceIntervalOptions();
  }
  function renderSourceIntervalOptions() {
    const root = byId('source-interval-options');
    if (!root) return;
    root.replaceChildren();
    const ids = $$('input[name="watch-source"]:checked').map(input => input.value);
    if (!ids.length) { root.append(el('small', '', 'Wähle zuerst eine Quelle.')); return; }
    for (const id of ids) {
      const entry = state.sourceIntervalDraft[id] || {};
      const row = el('div', 'source-interval-row');
      row.append(el('strong', '', (state.data?.sources || []).find(source => source.id === id)?.name || id));
      for (const [key, label] of [['normal_seconds', 'Normal (Sek.)'], ['black_friday_seconds', 'Black Friday (Sek.)']]) {
        const field = el('label', 'source-interval-input', label);
        const input = document.createElement('input'); input.type = 'number'; input.min = '300'; input.max = '86400'; input.step = '1'; input.placeholder = 'Automatisch'; input.value = entry[key] == null ? '' : String(entry[key]); input.dataset.sourceId = id; input.dataset.intervalKey = key;
        input.addEventListener('input', () => {
          const draft = { ...(state.sourceIntervalDraft[id] || {}) };
          if (input.value.trim() === '') delete draft[key]; else draft[key] = Number(input.value);
          if (Object.keys(draft).length) state.sourceIntervalDraft[id] = draft; else delete state.sourceIntervalDraft[id];
        });
        field.append(input); row.append(field);
      }
      root.append(row);
    }
  }
  function openWatchEditor(watch = null, offer = null) {
    clearForm();
    byId('watch-dialog-title').textContent = watch ? 'Beobachtung bearbeiten' : 'Neue Beobachtung';
    byId('watch-save').textContent = watch ? 'Änderungen speichern' : 'Beobachtung speichern';
    if (watch) {
      byId('watch-id').value = watch.id;
      byId('watch-name').value = watch.name || '';
      const radio = $(`input[name="watch-kind"][value="${watch.kind === 'discovery' ? 'discovery' : 'exact'}"]`); if (radio) radio.checked = true;
      byId('watch-query').value = watch.query || '';
      buildSourceOptions(state.data?.sources || [], watch.source_ids || []);
      byId('watch-product-id').value = watch.product_id || '';
      byId('watch-url').value = watch.url || '';
      byId('watch-gtin').value = watch.gtin || '';
      byId('watch-model').value = watch.model || '';
      byId('watch-variant').value = watch.variant || '';
      byId('watch-category').value = watch.category || '';
      byId('watch-manufacturer').value = watch.manufacturer || '';
      byId('watch-synonyms').value = (watch.synonyms || []).join(', ');
      byId('watch-exclusions').value = (watch.exclusions || []).join(', ');
      byId('watch-merchants-include').value = (watch.merchants_include ?? []).join(', ');
      byId('watch-merchants-exclude').value = (watch.merchants_exclude ?? []).join(', ');
      byId('watch-sellers-include').value = (watch.sellers_include ?? []).join(', ');
      byId('watch-sellers-exclude').value = (watch.sellers_exclude ?? []).join(', ');
      byId('watch-target').value = watch.target_cents == null ? '' : (watch.target_cents / 100).toFixed(2).replace('.', ',');
      byId('watch-max').value = watch.max_price_cents == null ? '' : (watch.max_price_cents / 100).toFixed(2).replace('.', ',');
      byId('watch-reference-max').value = watch.reference_max_price_cents == null ? '' : (watch.reference_max_price_cents / 100).toFixed(2).replace('.', ',');
      byId('watch-priority').value = watch.priority || 'normal';
      byId('watch-interval').value = watch.interval_seconds == null ? '' : watch.interval_seconds;
      byId('watch-bf-interval').value = watch.black_friday_interval_seconds == null ? '' : watch.black_friday_interval_seconds;
      state.sourceIntervalDraft = JSON.parse(JSON.stringify(watch.source_intervals || {}));
      renderSourceIntervalOptions();
      byId('watch-paused').checked = !!watch.paused;
    } else {
      buildSourceOptions(state.data?.sources || [], ['apple']);
    }
    if (offer) {
      const sourceId = String(offer.source || offer.source_id || '');
      byId('watch-dialog-title').textContent = 'Angebot exakt beobachten';
      byId('watch-name').value = String(offer.name || '').slice(0, 200);
      const exact = $('input[name="watch-kind"][value="exact"]'); if (exact) exact.checked = true;
      byId('watch-query').value = '';
      buildSourceOptions(state.data?.sources || [], [sourceId]);
      byId('watch-product-id').value = offer.product_id || '';
      byId('watch-url').value = offer.url || '';
      byId('watch-gtin').value = offer.gtin || '';
      byId('watch-model').value = offer.model || '';
      byId('watch-variant').value = offer.variant || '';
      byId('watch-manufacturer').value = offer.manufacturer || '';
    }
    dialog.showModal();
    byId('watch-name').focus();
  }
  function getWatchPayload() {
    const target = moneyInputToCents(byId('watch-target').value, 'Wunschpreis');
    const max = moneyInputToCents(byId('watch-max').value, 'Preislimit');
    const referenceMax = moneyInputToCents(byId('watch-reference-max').value, 'Referenzbudget');
    const rawInterval = byId('watch-interval').value.trim();
    const interval = rawInterval ? Number(rawInterval) : null;
    if (interval !== null && (!Number.isSafeInteger(interval) || interval < 300 || interval > 86400)) throw new Error('Prüfintervall muss zwischen 300 und 86400 Sekunden liegen.');
    const rawBfInterval = byId('watch-bf-interval').value.trim();
    const bfInterval = rawBfInterval ? Number(rawBfInterval) : null;
    if (bfInterval !== null && (!Number.isSafeInteger(bfInterval) || bfInterval < 300 || bfInterval > 86400)) throw new Error('Black-Friday-Intervall muss zwischen 300 und 86400 Sekunden liegen.');
    const sourceIds = $$('input[name="watch-source"]:checked').map(input => input.value);
    if (!sourceIds.length) throw new Error('Bitte mindestens eine konfigurierte Quelle auswählen.');
    const sourceIntervals = {};
    for (const sourceId of sourceIds) {
      const entry = state.sourceIntervalDraft[sourceId] || {};
      const normal = entry.normal_seconds;
      const blackFriday = entry.black_friday_seconds;
      if (normal == null && blackFriday == null) continue;
      if (normal == null || blackFriday == null) throw new Error(`Für ${sourceId} bitte beide quellenspezifischen Intervalle ausfüllen oder beide Felder leeren.`);
      for (const value of [normal, blackFriday]) {
        if (!Number.isSafeInteger(value) || value < 300 || value > 86400) throw new Error(`Quellenintervall für ${sourceId} muss zwischen 300 und 86400 Sekunden liegen.`);
      }
      sourceIntervals[sourceId] = { normal_seconds: normal, black_friday_seconds: blackFriday };
    }
    if ((byId('watch-product-id').value.trim() || byId('watch-url').value.trim()) && sourceIds.length !== 1) throw new Error('Produkt-ID oder URL benötigt genau eine Quelle.');
    const url = byId('watch-url').value.trim();
    if (url && !safeHttpUrl(url)) throw new Error('Produkt-URL muss eine gültige HTTP- oder HTTPS-Adresse ohne Zugangsdaten sein.');
    return {
      name: byId('watch-name').value.trim(),
      kind: $('input[name="watch-kind"]:checked')?.value || 'exact',
      query: byId('watch-query').value.trim(), source_ids: sourceIds,
      product_id: byId('watch-product-id').value.trim(), url,
      gtin: byId('watch-gtin').value.trim(), model: byId('watch-model').value.trim(), variant: byId('watch-variant').value.trim(),
      synonyms: splitList(byId('watch-synonyms').value, 5), category: byId('watch-category').value.trim(), manufacturer: byId('watch-manufacturer').value.trim(),
      exclusions: splitList(byId('watch-exclusions').value), merchants_include: splitList(byId('watch-merchants-include').value), merchants_exclude: splitList(byId('watch-merchants-exclude').value),
      sellers_include: splitList(byId('watch-sellers-include').value), sellers_exclude: splitList(byId('watch-sellers-exclude').value),
      target_cents: target, max_price_cents: max, reference_max_price_cents: referenceMax,
      priority: byId('watch-priority').value, interval_seconds: interval, black_friday_interval_seconds: bfInterval,
      source_intervals: sourceIntervals, paused: byId('watch-paused').checked,
    };
  }
  function openDiscoverySearch(query) {
    openWatchEditor();
    byId('watch-name').value = query.slice(0, 200);
    byId('watch-query').value = query;
    const discovery = $('input[name="watch-kind"][value="discovery"]');
    if (discovery) discovery.checked = true;
    const sources = (state.data?.sources || []).filter(source => source.configured !== false && source.supports_search === true && source.id !== 'apple').map(source => source.id);
    buildSourceOptions(state.data?.sources || [], sources);
    if (!sources.length) byId('watch-form-error').textContent = 'Der Dienst meldet derzeit keine eingerichteten Suchquellen.';
  }
  function renderResolvedOffers(result) {
    const root = byId('resolve-results'); root.replaceChildren();
    byId('resolve-source').textContent = result.source_name || result.source_id || '';
    for (const warning of (result.warnings || [])) root.append(el('p', 'resolve-warning', warning));
    const offers = Array.isArray(result.offers) ? result.offers : [];
    if (!offers.length) root.append(el('p', 'resolve-empty', 'Für diesen Link wurden keine passenden Produkte gefunden.'));
    offers.forEach(offer => {
      const card = el('article', 'resolve-card');
      const title = el('strong', '', offer.name || 'Unbenanntes Produkt');
      const isReference = offer.offer_type === 'reference' || offer.provenance === 'reference';
      const total = Number.isInteger(offer.price_cents) && Number.isInteger(offer.shipping_cents) ? offer.price_cents + offer.shipping_cents : null;
      const availability = ({ in_stock: 'Auf Lager', out_of_stock: 'Nicht auf Lager', unknown: 'Verfügbarkeit unbekannt' })[offer.availability] || 'Verfügbarkeit unbekannt';
      const meta = el('small', '', [isReference ? 'Referenzpreis · kein Händlerangebot' : (offer.seller || offer.merchant || 'Händler unbekannt'), offer.price_cents == null ? 'Artikelpreis unbekannt' : `${fmtMoney(offer.price_cents, offer.currency)} Artikel`, isReference ? 'Versand unbekannt' : offer.shipping_cents == null ? 'Versand unbekannt' : `Versand ${fmtMoney(offer.shipping_cents, offer.currency)}`, total == null || isReference ? 'Gesamt unbekannt' : `Gesamt ${fmtMoney(total, offer.currency)}`, availability, offer.gtin ? `GTIN ${offer.gtin}` : '', offer.model ? `Modell ${offer.model}` : '', offer.variant ? `Variante ${offer.variant}` : ''].filter(Boolean).join(' · '));
      const choose = el('button', 'button button-primary', 'Dieses Produkt beobachten'); choose.type = 'button';
      choose.addEventListener('click', () => { resolveDialog.close(); openWatchEditor(null, { ...offer, source: offer.source || result.source_id, url: offer.url || byId('quick-url').value.trim() }); });
      card.append(title, meta, choose);
      const offerUrl = safeHttpsUrl(offer.url || byId('quick-url').value.trim());
      if (offerUrl) { const link = el('a', 'resolve-link', 'Produktseite öffnen ↗'); link.href = offerUrl; link.target = '_blank'; link.rel = 'noopener noreferrer'; card.append(link); }
      root.append(card);
    });
    resolveDialog.showModal();
  }
  async function resolveProductUrl(event) {
    event.preventDefault();
    const url = byId('quick-url').value.trim();
    if (!url || !safeHttpUrl(url) || new URL(url).protocol !== 'https:') { toast('Bitte einen gültigen HTTPS-Produktlink eingeben.', 'error'); return; }
    const button = $('#quick-url-form button'); button.disabled = true;
    try { renderResolvedOffers(await api('/links/resolve', { method: 'POST', body: { url } })); }
    catch (error) { toast(error.message, 'error'); }
    finally { button.disabled = false; }
  }
  async function saveWatch(event) {
    event.preventDefault();
    if (state.formBusy) return;
    const errorNode = byId('watch-form-error'); errorNode.textContent = '';
    let payload;
    try { payload = getWatchPayload(); }
    catch (error) { errorNode.textContent = error.message; return; }
    if (!payload.name) { errorNode.textContent = 'Bitte einen Anzeigenamen eingeben.'; byId('watch-name').focus(); return; }
    if (payload.kind === 'discovery' && !payload.query) { errorNode.textContent = 'Der Entdecken-Modus benötigt eine Suchanfrage.'; byId('watch-query').focus(); return; }
    if (payload.kind === 'exact' && !payload.query && !payload.product_id && !payload.url && !payload.gtin && !payload.model) {
      errorNode.textContent = 'Für eine exakte Beobachtung Suchanfrage, Produkt-ID, URL, GTIN oder Modell eintragen. Für Titelabgleich den Produktnamen auch unter Suchanfrage eingeben.';
      byId('watch-query').focus(); return;
    }
    if (payload.target_cents != null && payload.max_price_cents != null && payload.target_cents > payload.max_price_cents) { errorNode.textContent = 'Der Wunschpreis liegt über dem Preislimit.'; return; }
    state.formBusy = true; byId('watch-save').disabled = true;
    try {
      const id = byId('watch-id').value;
      await api(id ? `/watches/${encodeURIComponent(id)}` : '/watches', { method: id ? 'PUT' : 'POST', body: payload });
      dialog.close(); toast(id ? 'Beobachtung aktualisiert.' : 'Beobachtung angelegt.', 'success'); await refresh();
    } catch (error) { errorNode.textContent = error.message; }
    finally { state.formBusy = false; byId('watch-save').disabled = false; }
  }
  async function removeWatch(watch) {
    if (!window.confirm(`„${watch.name}“ und die zugehörige Beobachtung entfernen?`)) return;
    try { await api(`/watches/${encodeURIComponent(watch.id)}`, { method: 'DELETE' }); toast('Beobachtung entfernt.', 'success'); await refresh(); }
    catch (error) { toast(`Entfernen fehlgeschlagen: ${error.message}`, 'error'); }
  }
  async function manualCheck(watch, button) {
    button.disabled = true; const original = button.textContent; button.textContent = 'Läuft …';
    try {
      const result = await api(`/watches/${encodeURIComponent(watch.id)}/check`, { method: 'POST', body: {} });
      if (result.status === 'ok') toast(`Prüfung abgeschlossen · ${Number(result.offers || 0)} Treffer.`, 'success');
      else toast(`Prüfung ${result.status || 'beendet'}${result.error ? `: ${result.error}` : '.'}`, result.status === 'busy' ? '' : 'error');
      await refresh();
    } catch (error) { toast(`Prüfung fehlgeschlagen: ${error.message}`, 'error'); }
    finally { button.textContent = original; }
  }
  async function showHistory(offer, watch) {
    const historyDialog = byId('history-dialog');
    byId('history-title').textContent = `Preisverlauf · ${offer.name || watch.name || 'Produkt'}`;
    const isReference = isReferenceOffer(offer);
    byId('history-subtitle').textContent = isReference ? 'Referenzpreis ohne Versand · kein Händlerangebot und kein Gesamtpreis' : `${offer.merchant || offer.source || 'Quelle'} · Gesamtpreis nur bei bekanntem Versand`;
    const rows = byId('history-rows'); rows.replaceChildren();
    byId('history-chart').replaceChildren();
    try {
      const history = await api(`/offers/${encodeURIComponent(offer.id)}/history`);
      const observations = Array.isArray(history) ? history : [];
      for (const item of observations) {
        const row = document.createElement('tr');
        const fromFeed = item.provenance === 'feed';
        const values = [`${fromFeed ? 'Katalog-Beobachtung · ' : ''}${fmtTime(item.checked_at)}`, fmtMoney(item.price_cents), item.shipping_cents == null ? 'Unbekannt' : fmtMoney(item.shipping_cents), item.total_cents == null ? 'Unbekannt' : fmtMoney(item.total_cents), availabilityLabel(item.availability, item.provenance), item.provenance || 'Quellenbeobachtung'];
        values.forEach(value => row.append(el('td', '', value)));
        rows.append(row);
      }
      renderChart(observations, isReference);
      if (!observations.length) rows.append(emptyTableRow('Für dieses Angebot liegen noch keine gespeicherten Beobachtungen vor.'));
    } catch (error) { rows.append(emptyTableRow(`Verlauf konnte nicht geladen werden: ${error.message}`)); showChartNote('Verlauf konnte nicht geladen werden.'); }
    historyDialog.showModal();
  }
  function availabilityLabel(value, provenance = '') {
    const fromFeed = provenance === 'feed';
    if (value === 'in_stock') return fromFeed ? 'Feed meldet verfügbar' : 'Verfügbar';
    if (value === 'out_of_stock') return fromFeed ? 'Feed meldet nicht verfügbar' : 'Nicht verfügbar';
    return fromFeed ? 'Feed: Verfügbarkeit unbekannt' : 'Unbekannt';
  }
  function emptyTableRow(message) { const row = document.createElement('tr'); const cell = el('td', '', message); cell.colSpan = 6; row.append(cell); return row; }
  function showChartNote(message) {
    const root = byId('history-chart'); root.replaceChildren();
    const svg = svgEl('svg', { viewBox: '0 0 700 220', role: 'img', 'aria-label': message });
    const text = svgEl('text', { x: 350, y: 112, 'text-anchor': 'middle', class: 'chart-note' }); text.textContent = message; svg.append(text); root.append(svg);
  }
  function renderChart(observations, reference = false) {
    const valueKey = reference ? 'price_cents' : 'total_cents';
    const known = observations.filter(item => Number.isInteger(item[valueKey]) && item[valueKey] >= 0 && Number.isFinite(item.checked_at)).sort((a, b) => a.checked_at - b.checked_at);
    if (known.length < 2) { showChartNote(known.length === 1 ? `${reference ? 'Eine Referenzpreis-' : 'Eine Gesamtpreis-'}beobachtung: Für einen Verlauf sind mindestens zwei nötig.` : `Noch keine ausreichenden Daten mit bekanntem ${reference ? 'Referenzpreis' : 'Gesamtpreis'}.`); return; }
    const width = 700, height = 220, pad = { left: 64, right: 20, top: 20, bottom: 37 };
    const minT = known[0].checked_at, maxT = known[known.length - 1].checked_at;
    let minP = Math.min(...known.map(item => item[valueKey])), maxP = Math.max(...known.map(item => item[valueKey]));
    if (minP === maxP) { minP = Math.max(0, minP - 100); maxP += 100; }
    const x = (item) => pad.left + (maxT === minT ? .5 : (item.checked_at - minT) / (maxT - minT)) * (width - pad.left - pad.right);
    const y = (item) => pad.top + (1 - (item[valueKey] - minP) / (maxP - minP)) * (height - pad.top - pad.bottom);
    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `Beobachteter ${reference ? 'Referenzpreis ohne Versand' : 'Gesamtpreis'} in ${known.length} Messungen; von ${fmtMoney(known[0][valueKey])} bis ${fmtMoney(known[known.length - 1][valueKey])}.` });
    for (let i = 0; i < 4; i++) {
      const ratio = i / 3, gy = pad.top + ratio * (height - pad.top - pad.bottom);
      svg.append(svgEl('line', { x1: pad.left, x2: width - pad.right, y1: gy, y2: gy, class: 'chart-grid' }));
      const value = Math.round(maxP - ratio * (maxP - minP));
      const label = svgEl('text', { x: pad.left - 8, y: gy + 4, 'text-anchor': 'end' }); label.textContent = fmtMoney(value); svg.append(label);
    }
    const points = known.map(item => `${x(item)},${y(item)}`).join(' ');
    const area = `${pad.left},${height - pad.bottom} ${points} ${width - pad.right},${height - pad.bottom}`;
    svg.append(svgEl('polygon', { points: area, class: 'chart-area' }));
    svg.append(svgEl('polyline', { points, class: 'chart-line' }));
    known.forEach((item) => {
      const circle = svgEl('circle', { cx: x(item), cy: y(item), r: 4.5, class: 'chart-point' }); circle.setAttribute('aria-label', `${fmtTime(item.checked_at)}: ${fmtMoney(item[valueKey])}${reference ? ' Referenzpreis ohne Versand' : ''}`); svg.append(circle);
    });
    const first = svgEl('text', { x: pad.left, y: height - 11 }); first.textContent = fmtTime(minT, { dateStyle: 'short', timeStyle: undefined }); svg.append(first);
    const last = svgEl('text', { x: width - pad.right, y: height - 11, 'text-anchor': 'end' }); last.textContent = fmtTime(maxT, { dateStyle: 'short', timeStyle: undefined }); svg.append(last);
    byId('history-chart').replaceChildren(svg);
  }
  async function updateSettings(key, value, feedback) {
    const status = byId('settings-feedback');
    try {
      const settings = { [key]: value };
      await api('/settings', { method: 'PUT', body: settings });
      status.textContent = feedback;
      await refresh();
    } catch (error) { status.textContent = `Einstellung konnte nicht gespeichert werden: ${error.message}`; toast(status.textContent, 'error'); await refresh(); }
  }
  async function saveNotificationConfiguration(event) {
    event.preventDefault();
    if (state.notificationConfigBusy) return;
    const field = byId('notification-webhook');
    const webhookUrl = field.value.trim();
    if (!safeHttpsUrl(webhookUrl)) { state.notificationFeedback = 'Bitte eine gültige HTTPS-Webhook-Adresse eingeben.'; byId('settings-feedback').textContent = state.notificationFeedback; field.focus(); return; }
    state.notificationConfigBusy = true;
    renderSettings(state.data || {});
    try {
      await api('/notifications/config', { method: 'PUT', body: { webhook_url: webhookUrl } });
      field.value = '';
      state.notificationFeedback = 'Webhook lokal gespeichert. Benachrichtigungen bleiben separat geschaltet.';
      await refresh();
    } catch {
      state.notificationFeedback = 'Webhook konnte nicht gespeichert werden. Prüfe die Adresse und versuche es erneut.';
    } finally {
      state.notificationConfigBusy = false;
      if (state.data) renderSettings(state.data);
      byId('settings-feedback').textContent = state.notificationFeedback;
    }
  }
  async function removeNotificationConfiguration() {
    if (state.notificationConfigBusy) return;
    state.notificationConfigBusy = true;
    renderSettings(state.data || {});
    try {
      await api('/notifications/config', { method: 'DELETE' });
      byId('notification-webhook').value = '';
      state.notificationFeedback = 'Lokale Webhook-Konfiguration entfernt.';
      await refresh();
    } catch {
      state.notificationFeedback = 'Lokale Webhook-Konfiguration konnte nicht entfernt werden.';
    } finally {
      state.notificationConfigBusy = false;
      if (state.data) renderSettings(state.data);
      byId('settings-feedback').textContent = state.notificationFeedback;
    }
  }
  function bind() {
    byId('watch-form').addEventListener('submit', saveWatch);
    byId('quick-name-form').addEventListener('submit', event => { event.preventDefault(); const query = byId('quick-name').value.trim(); if (query) openDiscoverySearch(query); });
    byId('quick-url-form').addEventListener('submit', resolveProductUrl);
    byId('new-watch-button').addEventListener('click', () => openWatchEditor());
    byId('new-watch-button-secondary').addEventListener('click', () => openWatchEditor());
    $$('.close-dialog').forEach(button => button.addEventListener('click', () => button.closest('dialog')?.close()));
    [dialog, resolveDialog, byId('history-dialog')].forEach(modal => modal.addEventListener('click', event => { if (event.target === modal) modal.close(); }));
    $$('input[name="watch-kind"]').forEach(input => input.addEventListener('change', () => {
      const selected = $$('input[name="watch-source"]:checked').map(item => item.value);
      const discovery = $('input[name="watch-kind"][value="discovery"]')?.checked;
      const sources = state.data?.sources || [];
      buildSourceOptions(sources, discovery ? selected.filter(id => sources.find(source => source.id === id)?.supports_search !== false) : selected);
    }));
    byId('watch-search').addEventListener('input', event => { state.search = event.target.value; if (state.data) renderWatchList(state.data); });
    byId('refresh-button').addEventListener('click', () => refresh(true));
    byId('activity-refresh').addEventListener('click', () => refresh(true));
    byId('setting-paused').addEventListener('change', event => updateSettings('paused', event.target.checked, event.target.checked ? 'Automatische Prüfungen pausiert.' : 'Automatische Prüfungen fortgesetzt.'));
    byId('setting-bf').addEventListener('change', event => {
      const duration = state.bfDurationDraft;
      updateSettings('black_friday_until', event.target.checked ? Math.floor(Date.now() / 1000) + duration : 0, event.target.checked ? `Black-Friday-Modus für ${duration === 604800 ? '7 Tage' : '24 Stunden'} aktiviert.` : 'Black-Friday-Modus beendet.');
    });
    byId('setting-bf-duration').addEventListener('change', () => {
      state.bfDurationDraft = Number(byId('setting-bf-duration').value || 86400);
      if (byId('setting-bf').checked) {
        const duration = state.bfDurationDraft;
        updateSettings('black_friday_until', Math.floor(Date.now() / 1000) + duration, `Black-Friday-Modusdauer auf ${duration === 604800 ? '7 Tage' : '24 Stunden'} gesetzt.`);
      }
    });
    byId('setting-notifications').addEventListener('change', event => updateSettings('notification_enabled', event.target.checked, event.target.checked ? 'Benachrichtigungen aktiviert.' : 'Benachrichtigungen deaktiviert.'));
    byId('notification-config-form').addEventListener('submit', saveNotificationConfiguration);
    byId('notification-config-remove').addEventListener('click', removeNotificationConfiguration);
    $$('.nav-link').forEach(link => link.addEventListener('click', () => { $$('.nav-link').forEach(item => item.classList.toggle('active', item === link)); }));
  }
  bind();
  refresh();
  window.setInterval(() => refresh(), 10000);
})();
