document.addEventListener("DOMContentLoaded", function () {
    var hero = document.querySelector(".zw-hero");
    if (!hero) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    window.addEventListener("scroll", function () {
        hero.style.backgroundPosition = "center calc(50% + " + window.scrollY * 0.25 + "px)";
    }, { passive: true });
});
