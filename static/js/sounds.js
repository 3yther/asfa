// Subtle UI sounds — off by default, toggled via the nav button and
// remembered in localStorage. Pure WebAudio beeps, no asset files.
const SoundFX = (() => {
  let enabled = localStorage.getItem('asfa_sounds') === 'true';

  let ctx = null;
  function audio() {
    // Lazily create the AudioContext so we don't spin one up (and trip
    // browser autoplay warnings) until sounds are actually used.
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    }
    return ctx;
  }

  function beep(freq = 440, duration = 0.08, vol = 0.05, type = 'sine') {
    if (!enabled) return;
    const ac = audio();
    if (!ac) return;
    try {
      if (ac.state === 'suspended') ac.resume();
      const osc = ac.createOscillator();
      const gain = ac.createGain();
      osc.connect(gain);
      gain.connect(ac.destination);
      osc.frequency.value = freq;
      osc.type = type;
      gain.gain.setValueAtTime(vol, ac.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + duration);
      osc.start(ac.currentTime);
      osc.stop(ac.currentTime + duration);
    } catch (e) { /* audio unavailable — stay silent */ }
  }

  return {
    nav: () => beep(880, 0.06, 0.04, 'sine'),
    confirm: () => beep(1200, 0.1, 0.05, 'sine'),
    alert: () => beep(440, 0.15, 0.06, 'square'),
    toggle: () => {
      enabled = !enabled;
      localStorage.setItem('asfa_sounds', enabled);
      if (enabled) beep(880, 0.06, 0.04, 'sine'); // confirm the toggle audibly
      return enabled;
    },
    isEnabled: () => enabled,
  };
})();

// Wire to nav clicks (top nav tabs + bottom nav buttons) and sync the toggle
// button's icon with the persisted preference.
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.nav-tab, .nav-btn').forEach(el => {
    el.addEventListener('click', () => SoundFX.nav());
  });
  const btn = document.getElementById('sound-toggle');
  if (btn) btn.textContent = SoundFX.isEnabled() ? '🔊' : '🔇';
});

// Expose for inline handlers (the nav toggle button references window.SoundFX).
window.SoundFX = SoundFX;
