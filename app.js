(() => {
  const initial = [
    {s:'Sa', n:60, y:50, d:.45}, {s:'Ri', n:62, y:42, d:.45},
    {s:'Ga', n:64, y:34, d:.45}, {s:'Ma', n:65, y:26, d:.45},
    {s:'Pa', n:67, y:18, d:.45}, {s:'Ma', n:65, y:26, d:.45},
    {s:'Ga', n:64, y:34, d:.45}, {s:'Ri', n:62, y:42, d:.75}
  ];
  let notes = initial.map(n => ({...n}));
  let audioCtx, activeNodes = [], selected = -1, editing = false, toastTimer;
  const names = ['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'];
  const swaras = ['Sa','Ri','Ga','Ma','Pa','Dha','Ni','Sa'];
  const scaleOffsets = {C:0,D:2,G:7,A:-3};
  const noteContainer = document.querySelector('#source-notes');
  const pianoRoll = document.querySelector('#piano-roll');
  const keyboard = document.querySelector('#keyboard');
  const $ = s => document.querySelector(s);

  function midiName(m) { return `${names[((m%12)+12)%12]}${Math.floor(m/12)-1}`; }
  function midiFrequency(m) { return 440 * Math.pow(2, (m - 69) / 12); }
  function getAudio() {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    return audioCtx;
  }
  function tone(midi, start=0, duration=.4, velocity=.12) {
    const ctx = getAudio(), osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.type = 'triangle'; osc.frequency.value = midiFrequency(midi);
    gain.gain.setValueAtTime(.0001, ctx.currentTime + start);
    gain.gain.exponentialRampToValueAtTime(velocity, ctx.currentTime + start + .015);
    gain.gain.exponentialRampToValueAtTime(.0001, ctx.currentTime + start + duration);
    osc.connect(gain); gain.connect(ctx.destination);
    osc.start(ctx.currentTime + start); osc.stop(ctx.currentTime + start + duration + .03);
    activeNodes.push(osc);
    osc.onended = () => { activeNodes = activeNodes.filter(x => x !== osc); };
  }
  function stop() { activeNodes.forEach(n => { try { n.stop(); } catch {} }); activeNodes = []; document.querySelectorAll('.active').forEach(e => e.classList.remove('active')); }
  function playPhrase() { stop(); let t=0; notes.forEach((n,i)=>{tone(n.n,t,n.d);setTimeout(()=>activate(i),Math.max(0,t*1000));t+=n.d+.035;}); }
  function activate(i) {
    document.querySelectorAll('.roll-note').forEach(e=>e.classList.remove('active'));
    document.querySelectorAll('.note').forEach(e=>e.classList.remove('selected'));
    document.querySelectorAll('.key').forEach(e=>e.classList.remove('active'));
    const r=document.querySelector(`.roll-note[data-index="${i}"]`), n=document.querySelector(`.note[data-index="${i}"]`), k=document.querySelector(`.key[data-midi="${notes[i]?.n}"]`);
    r?.classList.add('active'); n?.classList.add('selected'); k?.classList.add('active');
  }
  function render() {
    noteContainer.innerHTML=''; pianoRoll.innerHTML='';
    notes.forEach((note,i)=>{
      const el=document.createElement('button'); el.className='note'+(selected===i?' selected':''); el.dataset.index=i; el.style.setProperty('--note-y',`${note.y}px`); el.setAttribute('aria-label',`${note.s}, ${midiName(note.n)}. Click to change pitch.`);
      el.innerHTML=`<span class="swara">${note.s}</span><span class="western">${midiName(note.n)}</span>`;
      el.addEventListener('click',()=>{selected=i;if(editing){const next=(note.n+1);note.n=next;note.s=swaras[(i+1)%swaras.length];note.y=Math.max(5,note.y-7);notify(`Changed to ${note.s} · ${midiName(note.n)}`);}else{tone(note.n);activate(i);}render();});
      noteContainer.appendChild(el);
      const chip=document.createElement('button');chip.className='roll-note';chip.dataset.index=i;chip.textContent=midiName(note.n);chip.title=`${note.s} — play note`;chip.addEventListener('click',()=>{tone(note.n);activate(i);selected=i;render();});pianoRoll.appendChild(chip);
    });
  }
  function buildKeyboard() {
    keyboard.innerHTML='';
    const whites=[60,62,64,65,67,69,71,72,74,76,77,79,81,83];
    whites.forEach((m,i)=>{
      const key=document.createElement('button'); key.className='key';key.dataset.midi=m;key.setAttribute('aria-label',`Play ${midiName(m)}`);key.textContent=midiName(m);
      key.addEventListener('click',()=>{tone(m,.0,.5,.15);keyboard.querySelectorAll('.key,.black-key').forEach(k=>k.classList.remove('active'));key.classList.add('active');});keyboard.appendChild(key);
      if(i<whites.length-1 && whites[i+1]-m===2){
        const black=m+1,b=document.createElement('button');b.className='black-key';b.style.left=`${((i+1)/whites.length)*100}%`;b.setAttribute('aria-label',`Play ${midiName(black)}`);b.addEventListener('click',()=>{tone(black,.0,.5,.15);keyboard.querySelectorAll('.key,.black-key').forEach(k=>k.classList.remove('active'));b.classList.add('active');});keyboard.appendChild(b);
      }
    });
  }
  function notify(message) { const toast=$('#toast');toast.textContent=message;toast.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>toast.classList.remove('show'),2200); }

  $('#play-piano').addEventListener('click',playPhrase);
  $('#play-source').addEventListener('click',playPhrase);
  $('#stop-piano').addEventListener('click',stop);
  $('#reset-btn').addEventListener('click',()=>{notes=initial.map(n=>({...n}));selected=-1;render();notify('Example phrase restored');});
  $('#add-note').addEventListener('click',()=>{if(notes.length>=12){notify('This sketch supports up to 12 notes');return;}const prev=notes.at(-1);notes.push({s:'Sa',n:prev.n+2,y:prev.y,d:.45});render();notify('Added a note to your melody');});
  $('#edit-toggle').addEventListener('click',e=>{editing=!editing;document.body.classList.toggle('editing',editing);e.currentTarget.querySelector('span').textContent=editing?'Done editing':'Edit melody';notify(editing?'Select notes to step their pitch upward':'Melody edit complete');});
  $('#key-select').addEventListener('change',e=>{const offset=scaleOffsets[e.target.value]||0;notes=notes.map((n,i)=>({...n,n:initial[i%initial.length].n+offset}));render();notify(`Piano sketch moved to ${e.target.value}`);});
  $('#download-btn').addEventListener('click',()=>{const data={title:'Carnatic melody — piano sketch',source:'Carnatic-inspired sargam phrase',target:'Piano',key:$('#key-select').value,notes:notes.map(n=>({sargam:n.s,pitch:midiName(n.n),midi:n.n,durationSeconds:n.d}))};const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='raga-to-keys-sketch.json';a.click();URL.revokeObjectURL(a.href);notify('Sketch saved as a JSON file');});
  const modal=$('#modal');$('#more-learn').addEventListener('click',()=>{modal.classList.add('open');modal.setAttribute('aria-hidden','false');});$('#learn-nav').addEventListener('click',()=>$('#learn-card').scrollIntoView({behavior:'smooth',block:'center'}));$('#modal-close').addEventListener('click',()=>{modal.classList.remove('open');modal.setAttribute('aria-hidden','true');});modal.addEventListener('click',e=>{if(e.target===modal){modal.classList.remove('open');modal.setAttribute('aria-hidden','true');}});document.addEventListener('keydown',e=>{if(e.key==='Escape'){modal.classList.remove('open');modal.setAttribute('aria-hidden','true');}});
  buildKeyboard();render();
})();
