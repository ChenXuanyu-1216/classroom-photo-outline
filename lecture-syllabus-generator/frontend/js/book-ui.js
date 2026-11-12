(() => {
    const root = document.getElementById('knowledge-flow-home');
    if (!root?.classList.contains('kf-book-skin')) return;
    const make = (tag, className, text) => {
        const node = document.createElement(tag);
        node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };
    const main = root.querySelector('.kf-main');
    const hero = make('section', 'book-hero');
    hero.innerHTML = `<div class="book-hero-copy"><h2>把课堂，整理成自己的知识。</h2><p>从照片里找回线索，让零散的知识，慢慢有了脉络。</p></div>
      <div class="book-knowledge-scene" aria-hidden="true"><canvas></canvas><div class="book-scene-photo"><img src="/css/assets/synthetic-slide.png" alt=""><span>合成示例</span></div><div class="book-scene-excerpt">从照片中找到线索<small>从原图，到理解</small></div><span class="book-scene-annotation">让每一条线索，都有来处。</span></div>`;
    main.before(hero);
    root.classList.add('kf-playing');

    function decorateView(section) {
        if (!section || section.dataset.bookDecorated) return;
        section.dataset.bookDecorated = 'true';
        section.classList.add('book-page');
        const heading = section.querySelector('.kf-heading');
        if (heading) {
            const label = make('span', 'book-folio', section.querySelector('#task-list') ? '留给未来的自己' : section.querySelector('#file-input') ? '你的课堂笔记' : '让每个知识点，都有来处');
            heading.prepend(label);
        }
        const workspace = section.querySelector('.kf-workspace');
        if (section.querySelector('#file-input') || section.querySelector('#task-list')) {
            const history = Boolean(section.querySelector('#task-list'));
            const layout = make('div', history ? 'book-history-layout' : 'book-upload-layout');
            const aside = make('aside', 'book-manuscript');
            aside.innerHTML = history ? `<span class="book-margin-note">知识不会停在某一页</span><h2>把散落的片刻，<br>连成自己的星图。</h2><p>你读过的、想过的、记下的，<br>都在这里，等下一次相遇。</p><span class="book-hand-note">旧的页边，藏着新的发现。</span>` : `<span class="book-margin-note">从照片，到自己的理解</span><h2>不止收藏，<br>也让知识发生联系。</h2><p>留下课堂的原话。<br>理清知识的脉络。<br>随时回到那张照片。</p><div class="book-study-words"><span>照片</span><span>线索</span><span>理解</span></div><span class="book-hand-note">每一页，都有来处。</span>`;
            workspace.before(layout);
            layout.append(workspace, aside);
        }
    }
    decorateView(main.querySelector('section'));

    // An optional presentation hook: the original router still owns URLs,
    // retained File objects, blocked uploads, error recovery, and browser Back.
    async function turnWorkspace(previous, target, container, page) {
        decorateView(target);
        const reverse = page === 'upload';
        const previousStyle = previous.getAttribute('style');
        const targetStyle = target.getAttribute('style');
        const oldInert = previous.inert;
        const targetInert = target.inert;
        previous.classList.add('book-turn-sheet');
        target.classList.add('book-turn-sheet');
        previous.style.cssText += ';position:absolute;inset:0 0 auto;visibility:visible;pointer-events:none';
        target.style.cssText += ';position:relative;visibility:visible;pointer-events:none';
        container.append(previous);
        previous.inert = target.inert = true;
        const from = previous.offsetHeight;
        const to = target.offsetHeight;
        const sheet = reverse ? target : previous;
        previous.style.zIndex = reverse ? '1' : '2';
        target.style.zIndex = reverse ? '2' : '1';
        container.classList.add('book-turning');
        container.style.height = `${from}px`;
        sheet.classList.add('book-flexing');
        try {
            const frames = reverse ? [{transform:'translateZ(0) rotateY(-100deg)'},{transform:'translateZ(0) rotateY(0deg)'}] : [{transform:'translateZ(0) rotateY(0deg)'},{transform:'translateZ(0) rotateY(-100deg)'}];
            const options = {duration:780,easing:'cubic-bezier(.25,.46,.45,.94)',fill:'both'};
            const turn = sheet.animate?.(frames, options);
            const height = container.animate?.([{height:`${from}px`},{height:`${to}px`}], options);
            await Promise.all([turn?.finished.catch(()=>{}),height?.finished.catch(()=>{})]);
            turn?.cancel();height?.cancel();
        } finally {
            previous.remove();
            previous.classList.remove('book-turn-sheet','book-flexing');
            target.classList.remove('book-turn-sheet','book-flexing');
            previousStyle === null ? previous.removeAttribute('style') : previous.setAttribute('style', previousStyle);
            targetStyle === null ? target.removeAttribute('style') : target.setAttribute('style', targetStyle);
            previous.inert = oldInert;target.inert = targetInert;
            container.style.removeProperty('height');
            container.classList.remove('book-turning');
        }
    }

    // Optional session thumbnails are purely visual; never replace File objects,
    // task payloads or the saved outline, and never block successful submission.
    function rememberTask(taskId) {
        try {
            // SQLite may reuse a deleted task ID. Never retain an older task's
            // thumbnails if this upload has no usable previews or storage fills.
            sessionStorage.removeItem(`classroom-book-${taskId}`);
            const fileRows = [...document.querySelectorAll('#file-list .kf-file-row')];
            const filenames = fileRows.map(row => row.querySelector('.file-name')?.textContent);
            const thumbs = fileRows.map(row => {
                const filename = row.querySelector('.file-name')?.textContent;
                if (!filename || filenames.filter(name=>name===filename).length!==1) return null;
                const image = row.querySelector('.book-upload-thumb');
                if (!image) return null;
                if (!image.complete || !image.naturalWidth) return null;
                const canvas = document.createElement('canvas');
                const scale = Math.min(1,420/image.naturalWidth);
                canvas.width = image.naturalWidth*scale;canvas.height = image.naturalHeight*scale;
                canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);
                return {filename,src:canvas.toDataURL('image/jpeg',.65)};
            }).filter(Boolean);
            if (thumbs.length) sessionStorage.setItem(`classroom-book-${taskId}`,JSON.stringify(thumbs));
        } catch { /* Browser storage is optional; the task has already succeeded. */ }
    }
    globalThis.ClassroomBookUI = {turnWorkspace,rememberTask};

    let speed = 1;
    try { speed = Number(localStorage.getItem('knowledge-flow-speed')) || 1; } catch {}
    speed = Math.min(3,Math.max(.25,speed));
    const speedButton = root.querySelector('.kf-speed-toggle');
    const speedPanel = root.querySelector('.kf-speed-panel');
    const speedInput = root.querySelector('.kf-speed-input');
    function closeSpeed(){if(speedPanel){speedPanel.hidden=true;speedButton.setAttribute('aria-expanded','false');}}
    function applySpeed(){
        if (!speedInput) return;
        speedInput.value=String(speed);
        speedInput.setAttribute('aria-valuetext',`${speed} 倍速度`);
        root.querySelector('.kf-speed-value').textContent=`${speed}×`;
        speedButton.textContent=`速度 ${speed}×`;
        hero.getAnimations?.({subtree:true}).forEach(animation=>animation.updatePlaybackRate(speed));
    }
    speedButton?.addEventListener('click',()=>{speedPanel.hidden=!speedPanel.hidden;speedButton.setAttribute('aria-expanded',String(!speedPanel.hidden));});
    speedInput?.addEventListener('input',()=>{speed=Math.min(3,Math.max(.25,Number(speedInput.value)||1));applySpeed();try{localStorage.setItem('knowledge-flow-speed',String(speed));}catch{}});
    document.addEventListener('click',event=>{if(!root.querySelector('.kf-motion-controls')?.contains(event.target))closeSpeed();});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&speedPanel&&!speedPanel.hidden){closeSpeed();speedButton.focus();}});
    applySpeed();

    const scene=hero.querySelector('.book-knowledge-scene');
    const canvas=scene.querySelector('canvas'),ctx=canvas.getContext('2d');
    const nodes=[[.13,.5,'课堂照片'],[.37,.22,'提取原句'],[.38,.78,'回溯来源'],[.65,.49,'知识脉络'],[.88,.18,'理解'],[.89,.78,'复习']];
    const links=[[0,1],[0,2],[1,3],[2,3],[3,4],[3,5],[4,5]];
    let width=0,height=0,frame=0,last=0,time=0;const pointer={x:-999,y:-999};
    function draw(now){
        if(now-last<32){frame=requestAnimationFrame(draw);return;}
        time+=Math.min((now-last)/1000,.1)*speed;last=now;
        ctx.clearRect(0,0,width,height);
        const compact=[[.1,.53],[.37,.12],[.35,.81],[.68,.65],[.9,.13],[.92,.86]];
        const points=nodes.map((n,i)=>{const p=width<400?compact[i]:n;let x=p[0]*width,y=p[1]*height+Math.sin(time*.28+i)*4;const d=Math.hypot(pointer.x-x,pointer.y-y);if(d<100){x+=(pointer.x-x)*.025;y+=(pointer.y-y)*.025;}return{x,y,label:n[2]};});
        links.forEach(([a,b],i)=>{const p=points[a],q=points[b],bend=(i%2?1:-1)*height*.14;ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.bezierCurveTo(p.x+(q.x-p.x)*.4,p.y+bend,q.x-(q.x-p.x)*.4,q.y-bend,q.x,q.y);ctx.strokeStyle='rgba(88,119,80,.28)';ctx.lineWidth=.75;ctx.setLineDash(i===1?[2,4]:[]);ctx.stroke();ctx.setLineDash([]);const u=(time*.055+i*.21)%1,v=1-u;ctx.beginPath();ctx.arc(v*v*v*p.x+3*v*v*u*(p.x+(q.x-p.x)*.4)+3*v*u*u*(q.x-(q.x-p.x)*.4)+u*u*u*q.x,v*v*v*p.y+3*v*v*u*(p.y+bend)+3*v*u*u*(q.y-bend)+u*u*u*q.y,1.6,0,Math.PI*2);ctx.fillStyle='#8eaa7e';ctx.fill();});
        points.forEach((p,i)=>{ctx.beginPath();ctx.arc(p.x,p.y,i===3?4:3,0,Math.PI*2);ctx.fillStyle=i===3?'#24483e':'#f7f4eb';ctx.fill();ctx.strokeStyle='#91a082';ctx.stroke();if(i===3){ctx.beginPath();ctx.arc(p.x,p.y,10+Math.sin(time*.6),0,Math.PI*2);ctx.strokeStyle='#a0ac8b55';ctx.stroke();}ctx.font=`${width<400?10:12}px "Noto Serif SC",SimSun,serif`;ctx.fillStyle='#829074';ctx.textAlign='center';ctx.fillText(p.label,p.x,p.y-16);});
        frame=requestAnimationFrame(draw);
    }
    function resizeScene(){cancelAnimationFrame(frame);const rect=scene.getBoundingClientRect(),ratio=Math.min(devicePixelRatio||1,2);width=rect.width;height=rect.height;canvas.width=width*ratio;canvas.height=height*ratio;ctx.setTransform(ratio,0,0,ratio,0,0);last=0;draw(performance.now());}
    new ResizeObserver(resizeScene).observe(scene);
    scene.addEventListener('pointermove',event=>{const rect=scene.getBoundingClientRect();pointer.x=event.clientX-rect.left;pointer.y=event.clientY-rect.top;});
    scene.addEventListener('pointerleave',()=>{pointer.x=pointer.y=-999;});
    resizeScene();

    // Source lookup is an added presentation capability, never an API change.
    async function mountSources(){
        const view=document.getElementById('outline-view');
        if(!view)return;
        const taskId=new URLSearchParams(location.search).get('task_id');
        if(!/^[1-9]\d*$/.test(taskId||''))return;
        const workspace=view.closest('.kf-workspace'),layout=make('div','book-outline-layout');
        workspace.before(layout);const sources=make('aside','book-source-panel');
        sources.append(make('h2','','课堂来源'),make('p','book-source-state','正在读取来源页…'));
        layout.append(sources,workspace);
        let thumbs=[];
        try{const stored=JSON.parse(sessionStorage.getItem(`classroom-book-${taskId}`)||'[]');if(Array.isArray(stored))thumbs=stored;}catch{}
        const rows=new Map();
        const compareLink=make('a','book-source-caption','打开原图对照与评价');
        compareLink.href='/evaluation.html?task_id='+taskId;
        function focusSource(number,tag){
            const button=rows.get(number);if(!button)return;
            rows.forEach(node=>node.classList.remove('book-source-selected'));
            button.classList.add('book-source-selected');
            compareLink.href='/evaluation.html?task_id='+taskId+'&page_no='+number;
            button.scrollIntoView({behavior:'smooth',block:'nearest'});
            button.animate?.([{boxShadow:'0 0 0 5px #24483e25'},{boxShadow:'0 0 0 1px #24483e44'}],{duration:700});
            tag?.closest('li')?.animate?.([{backgroundColor:'#dce8cf'},{backgroundColor:'transparent'}],{duration:1100});
        }
        function bindTags(){view.querySelectorAll('.source-tag').forEach(tag=>{if(tag.dataset.bookBound)return;tag.dataset.bookBound='true';tag.tabIndex=0;tag.setAttribute('role','button');tag.title='定位课堂来源页';const locate=()=>{const numbers=[...tag.textContent.matchAll(/P\s*(\d+)/g)];if(numbers.length)focusSource(Number(numbers[0][1]),tag);};tag.addEventListener('click',locate);tag.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();locate();}});});}
        new MutationObserver(bindTags).observe(view,{childList:true,subtree:true});bindTags();
        try{
            const response=await API.getPages(taskId);if(!response.ok)throw new Error();const data=await response.json();if(!Array.isArray(data.pages))throw new Error();
            sources.replaceChildren(make('h2','','课堂来源'));
            data.pages.forEach(page=>{const button=make('button','book-source-item');button.type='button';button.setAttribute('aria-label',`来源 P${page.page_no}：${page.filename}`);const cached=globalThis.ClassroomBookSources?.thumbnailForPage(thumbs,page,data.pages);if(cached){const image=make('img','');image.src=cached.src;image.alt=page.filename;button.append(image);}button.append(make('span','book-source-number',`P${page.page_no}`),make('small','',page.filename));button.addEventListener('click',()=>focusSource(page.page_no));rows.set(page.page_no,button);sources.append(button);});
            sources.append(make('p','book-source-caption',thumbs.length?'缩略图仅在本浏览器暂存；详细校对请打开原图。':'此浏览器没有暂存缩略图；可打开服务器保存的原图对照。'),compareLink);
        }catch{sources.replaceChildren(make('h2','','课堂来源'),make('p','book-source-caption','来源页暂时无法读取，大纲的编辑、保存和导出仍可使用。'));}
    }
    mountSources();
})();
