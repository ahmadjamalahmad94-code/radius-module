/* docs_guide.js — scrollspy خفيف لأدلة «كيف تستخدمني»: يبرز بند الفهرس
   الجانبي المقابل للخطوة الظاهرة في الشاشة. مشترك بين كل الأدلة. */
(function () {
  'use strict';
  var links = Array.prototype.slice.call(document.querySelectorAll('[data-dg-toc]'));
  if (!links.length || !('IntersectionObserver' in window)) return;
  var map = {};
  links.forEach(function (a) { map[a.getAttribute('href').slice(1)] = a; });
  var obs = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (!en.isIntersecting) return;
      links.forEach(function (a) { a.classList.remove('is-active'); });
      var a = map[en.target.id];
      if (a) a.classList.add('is-active');
    });
  }, { rootMargin: '-35% 0px -55% 0px' });
  Object.keys(map).forEach(function (id) {
    var el = document.getElementById(id);
    if (el) obs.observe(el);
  });
})();
