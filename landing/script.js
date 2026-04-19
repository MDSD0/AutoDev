const reduceMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');

function motionAllowed() {
  return !reduceMotionQuery.matches;
}

(function initTheme() {
  const root = document.documentElement;
  const toggle = document.getElementById('themeToggle');
  const toggleLabel = document.getElementById('themeToggleLabel');
  const toggleIcon = document.getElementById('themeToggleIcon');
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  const systemTheme = window.matchMedia('(prefers-color-scheme: light)');
  const storageKey = 'autodev-theme';

  function updateToggle(theme) {
    const nextTheme = theme === 'dark' ? 'light' : 'dark';
    if (toggleLabel) {
      toggleLabel.textContent = nextTheme.charAt(0).toUpperCase() + nextTheme.slice(1);
    }
    if (toggleIcon) {
      toggleIcon.style.background = theme === 'dark'
        ? 'linear-gradient(135deg, #fbbf24, #f97316)'
        : 'linear-gradient(135deg, #2563eb, #4f46e5)';
      toggleIcon.style.boxShadow = theme === 'dark'
        ? '0 0 0 4px rgba(251, 191, 36, 0.18)'
        : '0 0 0 4px rgba(37, 99, 235, 0.14)';
    }
    if (toggle) {
      toggle.setAttribute('aria-pressed', String(theme === 'light'));
    }
    if (themeMeta) {
      themeMeta.setAttribute('content', theme === 'light' ? '#f3f8ff' : '#06111f');
    }
  }

  function applyTheme(theme) {
    root.dataset.theme = theme;
    updateToggle(theme);
  }

  const storedTheme = localStorage.getItem(storageKey);
  applyTheme(storedTheme || (systemTheme.matches ? 'light' : 'dark'));

  if (toggle) {
    toggle.addEventListener('click', () => {
      const nextTheme = root.dataset.theme === 'light' ? 'dark' : 'light';
      localStorage.setItem(storageKey, nextTheme);
      applyTheme(nextTheme);
    });
  }

  systemTheme.addEventListener('change', (event) => {
    if (!localStorage.getItem(storageKey)) {
      applyTheme(event.matches ? 'light' : 'dark');
    }
  });
})();

(function initStarfield() {
  if (!motionAllowed()) return;

  const canvas = document.getElementById('starfield');
  const aura = document.getElementById('cursorAura');
  if (!canvas) return;

  const context = canvas.getContext('2d');
  if (!context) return;

  const pointer = {
    x: window.innerWidth / 2,
    y: window.innerHeight / 2,
    active: false,
    radius: 170,
  };

  const stars = [];
  const starCount = 190;
  let devicePixelRatioScale = Math.min(window.devicePixelRatio || 1, 2);
  let width = 0;
  let height = 0;
  let animationFrame = 0;

  function resize() {
    devicePixelRatioScale = Math.min(window.devicePixelRatio || 1, 2);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.floor(width * devicePixelRatioScale);
    canvas.height = Math.floor(height * devicePixelRatioScale);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    context.setTransform(devicePixelRatioScale, 0, 0, devicePixelRatioScale, 0, 0);
  }

  function createStars() {
    stars.length = 0;
    for (let index = 0; index < starCount; index += 1) {
      stars.push({
        x: Math.random() * width,
        y: Math.random() * height,
        size: Math.random() * 1.8 + 0.25,
        speed: Math.random() * 0.18 + 0.03,
        alpha: Math.random() * 0.55 + 0.18,
        drift: Math.random() * 0.018 + 0.006,
        phase: Math.random() * Math.PI * 2,
        vx: 0,
        vy: 0,
        hue: Math.random() > 0.68 ? 'cool' : 'neutral',
      });
    }
  }

  function draw(time) {
    context.clearRect(0, 0, width, height);

    for (const star of stars) {
      const wobble = Math.sin(time * 0.0004 + star.phase);
      star.x += wobble * star.drift;
      star.y -= star.speed;

      if (pointer.active) {
        const dx = star.x - pointer.x;
        const dy = star.y - pointer.y;
        const distance = Math.hypot(dx, dy) || 1;

        if (distance < pointer.radius) {
          const force = (1 - distance / pointer.radius) * 0.055;
          star.vx += (dx / distance) * force;
          star.vy += (dy / distance) * force;

          if (star.size > 1.05) {
            context.beginPath();
            context.moveTo(pointer.x, pointer.y);
            context.lineTo(star.x, star.y);
            context.strokeStyle = `rgba(98, 168, 255, ${0.12 * (1 - distance / pointer.radius)})`;
            context.lineWidth = 0.7;
            context.stroke();
          }
        }
      }

      star.vx *= 0.96;
      star.vy *= 0.96;
      star.x += star.vx;
      star.y += star.vy;

      if (star.y < -18) {
        star.y = height + 18;
        star.x = Math.random() * width;
      }
      if (star.x < -18) star.x = width + 18;
      if (star.x > width + 18) star.x = -18;

      const pulse = Math.sin(time * 0.001 + star.phase) * 0.18;
      const alpha = Math.max(0.1, star.alpha + pulse);
      const radius = Math.max(0.2, star.size + pulse * 0.8);
      const fill = star.hue === 'cool'
        ? `rgba(98, 168, 255, ${alpha})`
        : `rgba(255, 255, 255, ${alpha})`;

      context.beginPath();
      context.arc(star.x, star.y, radius, 0, Math.PI * 2);
      context.fillStyle = fill;
      context.fill();

      if (radius > 1.15) {
        const gradient = context.createRadialGradient(star.x, star.y, 0, star.x, star.y, radius * 3.6);
        gradient.addColorStop(0, star.hue === 'cool' ? `rgba(124, 109, 255, ${alpha * 0.22})` : `rgba(255, 255, 255, ${alpha * 0.13})`);
        gradient.addColorStop(1, 'transparent');
        context.beginPath();
        context.arc(star.x, star.y, radius * 3.6, 0, Math.PI * 2);
        context.fillStyle = gradient;
        context.fill();
      }
    }

    animationFrame = window.requestAnimationFrame(draw);
  }

  function updateAura(clientX, clientY, active) {
    if (!aura) return;
    aura.style.opacity = active ? '1' : '0';
    aura.style.left = `${clientX}px`;
    aura.style.top = `${clientY}px`;
  }

  resize();
  createStars();
  animationFrame = window.requestAnimationFrame(draw);

  window.addEventListener('resize', () => {
    resize();
    createStars();
  });

  window.addEventListener('pointermove', (event) => {
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    pointer.active = true;
    updateAura(event.clientX, event.clientY, true);
  }, { passive: true });

  window.addEventListener('pointerleave', () => {
    pointer.active = false;
    updateAura(pointer.x, pointer.y, false);
  });

  reduceMotionQuery.addEventListener('change', () => {
    if (!motionAllowed()) {
      window.cancelAnimationFrame(animationFrame);
      if (aura) aura.style.opacity = '0';
      context.clearRect(0, 0, width, height);
    }
  });
})();

(function initNavbar() {
  const navbar = document.getElementById('navbar');
  if (!navbar) return;

  function syncNavbar() {
    navbar.classList.toggle('scrolled', window.scrollY > 24);
  }

  syncNavbar();
  window.addEventListener('scroll', syncNavbar, { passive: true });
})();

(function initTerminal() {
  const body = document.getElementById('terminalBody');
  const typingText = document.getElementById('typingText');
  if (!body || !typingText) return;

  const cursor = body.querySelector('.term-cursor');
  const sequences = [
    { type: 'input', text: 'Build a polished chat app with auth and shared rooms' },
    { type: 'output', text: 'Analyzing request and drafting execution plan...', className: 'term-info' },
    { type: 'output', text: 'Spec ready: realtime backend, desktop-first UI, test plan', className: 'term-output' },
    { type: 'output', text: 'Planning files: server.py, app.js, index.html, style.css', className: 'term-file' },
    { type: 'output', text: 'Writing code and wiring dependencies...', className: 'term-output' },
    { type: 'output', text: 'Running build and integration checks...', className: 'term-info' },
    { type: 'output', text: 'Detected runtime issue. Applying patch and retrying...', className: 'term-file' },
    { type: 'output', text: 'Success: app is running and ready for handoff.', className: 'term-success' },
  ];

  let sequenceIndex = 0;

  function addOutputLine(text, className) {
    const line = document.createElement('div');
    line.className = 'term-line';

    const message = document.createElement('span');
    message.className = className;
    message.textContent = text;

    line.appendChild(message);
    line.style.opacity = '0';
    line.style.transform = 'translateY(8px)';
    body.appendChild(line);

    requestAnimationFrame(() => {
      line.style.transition = 'opacity 0.28s ease, transform 0.28s ease';
      line.style.opacity = '1';
      line.style.transform = 'translateY(0)';
    });

    body.scrollTop = body.scrollHeight;
  }

  function typeText(text, callback) {
    typingText.textContent = '';
    if (!motionAllowed()) {
      typingText.textContent = text;
      callback();
      return;
    }

    let characterIndex = 0;
    const interval = window.setInterval(() => {
      typingText.textContent += text[characterIndex] || '';
      characterIndex += 1;

      if (characterIndex >= text.length) {
        window.clearInterval(interval);
        window.setTimeout(callback, 420);
      }
    }, 28);
  }

  function resetTerminal() {
    Array.from(body.querySelectorAll('.term-line')).forEach((line, index) => {
      if (index > 0) line.remove();
    });
    typingText.textContent = '';
    sequenceIndex = 0;
    if (cursor) cursor.style.display = '';
  }

  function runSequence() {
    if (sequenceIndex >= sequences.length) {
      if (cursor) cursor.style.display = 'none';
      window.setTimeout(() => {
        resetTerminal();
        window.setTimeout(runSequence, 900);
      }, 2600);
      return;
    }

    const current = sequences[sequenceIndex];
    sequenceIndex += 1;

    if (current.type === 'input') {
      typeText(current.text, () => {
        if (cursor) cursor.style.display = 'none';
        window.setTimeout(runSequence, 260);
      });
      return;
    }

    addOutputLine(current.text, current.className);
    window.setTimeout(runSequence, motionAllowed() ? 520 : 120);
  }

  window.setTimeout(runSequence, 850);
})();

(function initScrollAnimations() {
  const elements = document.querySelectorAll('.feature-card, .workflow-step, .download-card, .download-requirements');
  if (!elements.length || !motionAllowed()) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry, index) => {
      if (!entry.isIntersecting) return;
      window.setTimeout(() => {
        entry.target.style.opacity = '1';
        entry.target.style.transform = 'translateY(0)';
      }, index * 70);
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.14 });

  elements.forEach((element) => {
    element.style.opacity = '0';
    element.style.transform = 'translateY(22px)';
    element.style.transition = 'opacity 0.55s ease, transform 0.55s ease';
    observer.observe(element);
  });
})();

(function initCounters() {
  const counters = document.querySelectorAll('.stat-number[data-count]');
  if (!counters.length) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;

      const element = entry.target;
      const target = Number.parseInt(element.dataset.count || '0', 10);
      let current = 0;
      const step = Math.max(1, Math.floor(target / 36));

      const timer = window.setInterval(() => {
        current += step;
        if (current >= target) {
          current = target;
          window.clearInterval(timer);
        }
        element.textContent = String(current);
      }, motionAllowed() ? 34 : 1);

      observer.unobserve(element);
    });
  }, { threshold: 0.5 });

  counters.forEach((counter) => observer.observe(counter));
})();

(function initInteractiveCards() {
  const cards = document.querySelectorAll('[data-sheen]');
  if (!cards.length) return;

  cards.forEach((card) => {
    card.addEventListener('pointermove', (event) => {
      const rect = card.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      card.style.setProperty('--glow-x', `${x}px`);
      card.style.setProperty('--glow-y', `${y}px`);
      card.classList.add('is-active');
    });

    card.addEventListener('pointerleave', () => {
      card.classList.remove('is-active');
    });
  });
})();

(function initOsDetect() {
  const userAgent = navigator.userAgent.toLowerCase();
  const macCard = document.querySelector('[data-platform="mac"]');
  const windowsCard = document.querySelector('[data-platform="windows"]');

  if (userAgent.includes('mac') && macCard) {
    macCard.classList.add('is-recommended');
  } else if (userAgent.includes('win') && windowsCard) {
    windowsCard.classList.add('is-recommended');
  }
})();

(function initSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener('click', (event) => {
      const target = document.querySelector(link.getAttribute('href') || '');
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: motionAllowed() ? 'smooth' : 'auto', block: 'start' });
    });
  });
})();
