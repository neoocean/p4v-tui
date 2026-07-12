// 스크린샷 클릭 → 확대 보기(라이트박스). 의존성 없음.
// 대상: 히어로/기능/갤러리/가이드의 스크린샷 이미지. 배경·닫기버튼 클릭 또는 Esc 로 닫는다.
(function () {
  var SEL = '.hero-shot img, .shot img, .gcard img, figure img';

  var box = document.createElement('div');
  box.className = 'lightbox';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  var close = document.createElement('button');
  close.className = 'lb-close';
  close.setAttribute('aria-label', '닫기');
  close.innerHTML = '×';            // ×
  var big = document.createElement('img');
  big.alt = '';
  box.appendChild(close);
  box.appendChild(big);

  function ready() {
    document.body.appendChild(box);

    document.addEventListener('click', function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      var img = t.closest(SEL);
      if (!img || box.contains(img)) return;   // 라이트박스 내부 이미지 클릭은 무시
      e.preventDefault();
      big.src = img.currentSrc || img.src;
      big.alt = img.alt || '';
      box.classList.add('open');
    });

    function hide() { box.classList.remove('open'); big.removeAttribute('src'); }
    box.addEventListener('click', hide);       // 배경·닫기버튼(자식) 클릭 모두 닫힘
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && box.classList.contains('open')) hide();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
