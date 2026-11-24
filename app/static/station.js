const layout = document.querySelector('#selection');
const scene = layout.querySelector('#scenario');
const originalScene = scene.value;
const work = layout.querySelector('#work-lot');
const archived = layout.querySelector('#scope-run');
scene.addEventListener('change', () => {
  const changed = scene.value !== originalScene;
  work.disabled = changed;
  if (archived) archived.disabled = changed;
});
document.querySelector('#print').addEventListener('click', () => print());
const commit = document.querySelector('#commit');
commit?.addEventListener('click', async () => {
  const message = document.querySelector('#message');
  const { key, sample, view } = commit.dataset;
  commit.disabled = true;
  message.textContent = '核对秤单中…';
  try {
    const response = await (key
      ? fetch(`/api/runs/${key}/rerun`, { method:'POST' })
      : fetch('/api/runs', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({sample}) }));
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || '卷联保存失败');
    if (key) message.textContent = '复算一致，称签仍用原卷。';
    else {
      const query = new URLSearchParams({ run:result.id, sample, work:work.value });
      location.assign(`/ledger/${view}?${query}`);
    }
  } catch (error) {
    message.textContent = `${error.message}，请重试。`;
  } finally { commit.disabled = false; }
});

const signingMessage = document.querySelector('#signing-message');
document.querySelectorAll('.issue-btn').forEach((button) => {
  button.addEventListener('click', async () => {
    const run = new URLSearchParams(location.search).get('run');
    if (!run || !signingMessage) return;
    button.disabled = true;
    signingMessage.textContent = `正在重算并签发 ${button.dataset.package}…`;
    try {
      const response = await fetch(`/api/signing/runs/${run}/issue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ packages: [button.dataset.package] }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || '签发失败');
      signingMessage.textContent = `${button.dataset.package} 已整克签发并锁定，卷联第 ${result.signed.length} 联。`;
      location.reload();
    } catch (error) {
      signingMessage.textContent = `${error.message}；旧版卷联保留。`;
      button.disabled = false;
    }
  });
});
