// Mark JS as active so CSS applies the hidden-until-revealed states.
// (Set as early as possible; an inline <head> script also sets it to avoid any flash.)
document.documentElement.classList.add('js');

// --- Nav shadow on scroll ---
const nav = document.getElementById('nav');
const onScroll = () => nav && nav.classList.toggle('scrolled', window.scrollY > 8);
window.addEventListener('scroll', onScroll, { passive: true });
onScroll();

// --- Scroll reveal (cards + .reveal blocks) ---
const revealGrid = (grid) =>
  [...grid.children].forEach((card, i) => {
    card.style.transitionDelay = ((i % 3) * 0.07 + Math.floor(i / 3) * 0.05).toFixed(2) + 's';
    card.classList.add('in');
  });

const targets = [...document.querySelectorAll('.grid, .reveal')];

if ('IntersectionObserver' in window) {
  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const el = entry.target;
      el.classList.contains('grid') ? revealGrid(el) : el.classList.add('in');
      io.unobserve(el);
    });
  }, { threshold: 0.14, rootMargin: '0px 0px -8% 0px' });
  targets.forEach((el) => io.observe(el));
} else {
  targets.forEach((el) => (el.classList.contains('grid') ? revealGrid(el) : el.classList.add('in')));
}

// Safety net: if anything is still hidden after 2.2s (observer stalled,
// throttled rAF, etc.), reveal it so content can never be stuck invisible.
setTimeout(() => {
  document.querySelectorAll('.reveal:not(.in)').forEach((el) => el.classList.add('in'));
  document.querySelectorAll('.grid').forEach((g) => {
    if ([...g.children].some((c) => !c.classList.contains('in'))) revealGrid(g);
  });
}, 2200);

// --- Subtle pointer parallax on tiles ---
document.querySelectorAll('.card').forEach((card) => {
  const tile = card.querySelector('.tile');
  if (!tile) return;
  card.addEventListener('pointermove', (e) => {
    const r = card.getBoundingClientRect();
    const dx = (e.clientX - r.left) / r.width - 0.5;
    const dy = (e.clientY - r.top) / r.height - 0.5;
    tile.style.transform = `translate(${dx * 6}px, ${dy * 6}px) scale(1.04)`;
  });
  card.addEventListener('pointerleave', () => { tile.style.transform = ''; });
});
