
(function(){
  const uploadStore = new Map();

  function closeMenus(){
    const g = document.getElementById('globalActionMenu');
    if(g) g.remove();
    document.querySelectorAll('.combo-panel.show').forEach(e=>e.classList.remove('show'));
  }

  function closeModal(el){
    (el.closest('.modal') || el).classList.remove('show');
  }

  function clamp(v,min,max){ return Math.max(min,Math.min(max,v)); }

  function placeBox(btn, pop, fixed){
    const r = btn.getBoundingClientRect();
    pop.style.visibility='hidden';
    pop.style.display='block';
    if(fixed){
      pop.style.position='fixed';
      pop.style.left='0px';
      pop.style.top='0px';
      pop.style.right='auto';
      pop.style.bottom='auto';
    }
    const pr = pop.getBoundingClientRect();
    const margin = 8;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const maxH = Math.max(160, vh - margin*2);
    pop.style.maxHeight = Math.min(460, maxH) + 'px';
    pop.style.overflowY = 'auto';

    let left = r.right - pr.width;
    left = clamp(left, margin, Math.max(margin, vw - pr.width - margin));
    let top = r.bottom + margin;
    const below = vh - r.bottom - margin;
    const above = r.top - margin;
    if(pr.height > below && above > below){
      top = r.top - Math.min(pr.height,maxH) - margin;
    }
    top = clamp(top, margin, Math.max(margin, vh - Math.min(pr.height,maxH) - margin));
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
    pop.style.visibility='visible';
  }

  function showActionMenu(btn, source){
    const old = document.getElementById('globalActionMenu');
    if(old) old.remove();

    const pop = document.createElement('div');
    pop.id = 'globalActionMenu';
    pop.className = 'menu-pop show global-action-menu';
    pop.innerHTML = source.innerHTML;
    document.body.appendChild(pop);
    placeBox(btn, pop, true);
  }

  function syncInput(input){
    const files = uploadStore.get(input) || [];
    const dt = new DataTransfer();
    files.forEach(f=>dt.items.add(f));
    input.files = dt.files;
  }

  function refreshFileList(input){
    const id = input.getAttribute('data-file-list');
    if(!id) return;
    const box = document.getElementById(id);
    if(!box) return;
    const files = uploadStore.get(input) || Array.from(input.files || []);
    uploadStore.set(input, files);
    box.innerHTML = '';
    files.forEach((f, idx)=>{
      const row = document.createElement('div');
      row.className='file-item';
      row.innerHTML='<span class="file-name">'+escapeHtml(f.name)+' <em>'+Math.ceil(f.size/1024)+' KB</em></span><button type="button" data-remove-file="'+idx+'">移除</button>';
      box.appendChild(row);
    });
    syncInput(input);
  }

  function escapeHtml(s){
    return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function updateShareTarget(pick){
    const form = pick.closest('form');
    if(!form) return;
    const t = form.querySelector('input[name=target_type]');
    const id = form.querySelector('input[name=target_id]');
    const label = form.querySelector('[data-share-label]');
    if(t) t.value = pick.dataset.type || 'link';
    if(id) id.value = pick.dataset.id || '';
    if(label) label.textContent = pick.dataset.label || pick.textContent.trim();
    form.querySelectorAll('.share-pick.active').forEach(x=>x.classList.remove('active'));
    pick.classList.add('active');
  }

  document.addEventListener('change', function(e){
    if(e.target.matches('input[type=file][data-folder-input]')){
      const key=e.target.getAttribute('data-folder-input');
      const target=document.querySelector('[data-folder-target="'+key+'"]');
      const f=e.target.files && e.target.files[0];
      if(target && f && f.webkitRelativePath){
        target.value=f.webkitRelativePath.split('/')[0];
      }
    }

    if(e.target.matches('input[type=file][data-file-list]')){
      const input=e.target;
      const old=uploadStore.get(input) || [];
      const add=Array.from(input.files || []);
      const merged=old.slice();
      add.forEach(f=>{
        if(!merged.some(x=>x.name===f.name && x.size===f.size && x.lastModified===f.lastModified)){
          merged.push(f);
        }
      });
      uploadStore.set(input, merged);
      refreshFileList(input);
    }

    if(e.target.matches('[data-fill-list]')){
      const target=document.getElementById(e.target.getAttribute('data-fill-list'));
      if(target){
        const values=Array.from(document.querySelectorAll('[data-fill-list="'+e.target.getAttribute('data-fill-list')+'"]:checked')).map(x=>x.value).filter(Boolean);
        target.value=values.join(',');
      }
    }
  });

  document.addEventListener('input', function(e){
    if(e.target.matches('[data-tree-search]')){
      const tree=document.getElementById(e.target.getAttribute('data-tree-search'));
      if(!tree) return;
      const q=e.target.value.trim().toLowerCase();
      tree.querySelectorAll('.tree-line').forEach(line=>{
        const hit=!q || line.textContent.toLowerCase().includes(q);
        line.style.display=hit?'flex':'none';
        if(hit){
          let p=line.parentElement;
          while(p && p!==tree){
            if(p.classList && p.classList.contains('tree-children')){
              p.classList.add('open');
              const group=p.closest('[data-tree-group]');
              if(group){
                group.classList.add('open');
                const b=group.querySelector(':scope > .tree-line [data-tree-toggle]');
                if(b) b.textContent='▾';
              }
            }
            p=p.parentElement;
          }
        }
      });
    }
  });

  document.addEventListener('click', function(e){
    const copyBtn=e.target.closest('[data-copy-text]');
    if(copyBtn){
      e.preventDefault();
      const text=copyBtn.getAttribute('data-copy-text')||'';
      if(navigator.clipboard){ navigator.clipboard.writeText(text); }
      else { const ta=document.createElement('textarea'); ta.value=text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); }
      copyBtn.textContent='已复制';
      setTimeout(()=>closeMenus(),500);
      return;
    }

    const remove=e.target.closest('[data-remove-file]');
    if(remove){
      const list=remove.closest('.file-list');
      const input=list && document.querySelector('[data-file-list="'+list.id+'"]');
      if(input){
        const files=uploadStore.get(input) || [];
        files.splice(parseInt(remove.getAttribute('data-remove-file'),10),1);
        uploadStore.set(input,files);
        refreshFileList(input);
      }
      return;
    }

    const togg=e.target.closest('[data-tree-toggle]');
    if(togg){
      e.preventDefault();
      const group=togg.closest('[data-tree-group]');
      if(group){
        group.classList.toggle('open');
        togg.textContent=group.classList.contains('open')?'▾':'▸';
      }
      return;
    }

    const sharePick=e.target.closest('.share-pick');
    if(sharePick){
      e.preventDefault();
      updateShareTarget(sharePick);
      closeMenus();
      return;
    }

    const pick=e.target.closest('.tree-node.pick');
    if(pick){
      document.querySelectorAll('.tree-node.pick.active').forEach(x=>x.classList.remove('active'));
      pick.classList.add('active');
      const st=document.getElementById('permSubjectType');
      const si=document.getElementById('permSubjectId');
      const pl=document.getElementById('permPicked');
      if(st) st.value=pick.dataset.type;
      if(si) si.value=pick.dataset.id;
      if(pl) pl.textContent=pick.dataset.label || pick.textContent.trim();
      closeMenus();
      return;
    }

    const combo=e.target.closest('[data-dropdown]');
    if(combo){
      e.preventDefault();
      const panel=document.getElementById(combo.getAttribute('data-dropdown'));
      const was=panel && panel.classList.contains('show');
      closeMenus();
      if(panel && !was){
        panel.classList.add('show');
      }
      return;
    }

    const menuBtn=e.target.closest('[data-menu]');
    if(menuBtn){
      e.preventDefault();
      const src=document.getElementById(menuBtn.getAttribute('data-menu'));
      if(src){
        closeMenus();
        showActionMenu(menuBtn,src);
      }
      return;
    }

    const modalBtn=e.target.closest('[data-modal]');
    if(modalBtn){
      e.preventDefault();
      const m=document.getElementById(modalBtn.getAttribute('data-modal'));
      closeMenus();
      if(m) m.classList.add('show');
      return;
    }

    if(e.target.matches('[data-close]')){
      e.preventDefault();
      closeModal(e.target);
      return;
    }

    if(e.target.classList.contains('modal')){
      e.target.classList.remove('show');
      return;
    }

    if(!e.target.closest('.menu-pop') && !e.target.closest('.combo-panel')){
      closeMenus();
    }
  });

  window.addEventListener('resize', closeMenus);
  window.addEventListener('scroll', closeMenus, true);

  document.addEventListener('keydown', function(e){
    if(e.key==='Escape'){
      closeMenus();
      document.querySelectorAll('.modal.show').forEach(m=>m.classList.remove('show'));
    }
  });
})();
