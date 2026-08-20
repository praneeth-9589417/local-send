const state = { room: null, ownerToken: null, inviteBaseUrl: null };

const elements = {
  home: document.querySelector('#home-view'),
  room: document.querySelector('#room-view'),
  roomCode: document.querySelector('#active-room-code'),
  codeInput: document.querySelector('#room-code-input'),
  create: document.querySelector('#create-room-button'),
  newRoomMode: document.querySelector('#new-room-mode'),
  joinForm: document.querySelector('#join-form'),
  ownerPanel: document.querySelector('#owner-panel'),
  guestNote: document.querySelector('#guest-note'),
  ownerControls: document.querySelector('#owner-controls'),
  roomMode: document.querySelector('#room-mode-select'),
  saveRoomMode: document.querySelector('#save-room-mode-button'),
  deleteRoom: document.querySelector('#delete-room-button'),
  fileInput: document.querySelector('#file-input'),
  upload: document.querySelector('#upload-button'),
  files: document.querySelector('#files-list'),
  transfer: document.querySelector('#transfer-status'),
  addressNote: document.querySelector('#address-note'),
};

function roomTokenKey(code) { return `local-send-owner:${code}`; }
function cleanCode(value) { return value.toUpperCase().replace(/[^A-Z2-9]/g, '').slice(0, 8); }
function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** power).toFixed(power ? 1 : 0)} ${units[power]}`;
}
function formatDate(iso) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso));
}
function status(message, error = false) {
  elements.transfer.hidden = !message;
  elements.transfer.textContent = message || '';
  elements.transfer.classList.toggle('error', error);
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (_) {
    throw new Error('Cannot reach the Local Send host. Check that the laptop is running and both devices are on the same Wi‑Fi.');
  }
  const contentType = response.headers.get('content-type') || '';
  const body = contentType.includes('application/json') ? await response.json() : {};
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status}).`);
  return body;
}

function showHome() {
  state.room = null;
  state.ownerToken = null;
  elements.home.hidden = false;
  elements.room.hidden = true;
  status('');
  history.replaceState(null, '', window.location.pathname);
}

function showRoom(room, ownerToken = null) {
  state.room = room;
  try { state.ownerToken = ownerToken || localStorage.getItem(roomTokenKey(room.code)); }
  catch (_) { state.ownerToken = ownerToken; }
  const isOwner = Boolean(state.ownerToken);
  const canUpload = isOwner || room.access_mode === 'collaborative';
  elements.home.hidden = true;
  elements.room.hidden = false;
  elements.roomCode.textContent = room.code;
  elements.ownerPanel.hidden = !canUpload;
  elements.ownerControls.hidden = !isOwner;
  elements.guestNote.hidden = canUpload;
  elements.roomMode.value = room.access_mode;
  elements.fileInput.value = '';
  elements.upload.disabled = true;
  renderFiles(room.files);
}

function renderFiles(files) {
  elements.files.replaceChildren();
  if (!files.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    const canUpload = Boolean(state.ownerToken) || state.room.access_mode === 'collaborative';
    empty.textContent = canUpload ? 'No files yet. Choose files above to share them.' : 'No files have been shared in this room yet.';
    elements.files.append(empty);
    return;
  }
  const template = document.querySelector('#file-template');
  for (const file of files) {
    const row = template.content.cloneNode(true);
    row.querySelector('.file-name').textContent = file.original_name;
    row.querySelector('.file-meta').textContent = `${formatBytes(file.size_bytes)} · Added ${formatDate(file.uploaded_at)}`;
    const button = document.createElement('button');
    button.className = 'download-button';
    button.textContent = 'Download';
    button.addEventListener('click', () => downloadFile(file, button));
    const action = row.querySelector('.file-action');
    action.append(button);
    if (state.ownerToken) {
      const deleteButton = document.createElement('button');
      deleteButton.className = 'delete-file-button';
      deleteButton.type = 'button';
      deleteButton.title = `Delete ${file.original_name}`;
      deleteButton.setAttribute('aria-label', `Delete ${file.original_name}`);
      deleteButton.textContent = '🗑';
      deleteButton.addEventListener('click', () => deleteFile(file, deleteButton));
      action.append(deleteButton);
    }
    elements.files.append(row);
  }
}

async function loadRoom(code, ownerToken = null) {
  code = cleanCode(code);
  if (code.length !== 8) throw new Error('Enter the complete 8-character room code.');
  status('Opening room…');
  const room = await api(`/api/rooms/${encodeURIComponent(code)}`);
  showRoom(room, ownerToken);
  history.replaceState(null, '', `#room=${room.code}`);
  status('');
}

async function createRoom() {
  elements.create.disabled = true;
  status('Creating your room…');
  try {
    const room = await api('/api/rooms', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_mode: elements.newRoomMode.value }),
    });
    // Storage is a convenience for returning to a room after a refresh. A room
    // must still work in privacy modes where localStorage is unavailable.
    try { localStorage.setItem(roomTokenKey(room.code), room.owner_token); } catch (_) { /* keep the key for this session */ }
    await loadRoom(room.code, room.owner_token);
  } catch (error) {
    status(error.message, true);
  } finally {
    elements.create.disabled = false;
  }
}

function uploadOne(file) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', `/api/rooms/${encodeURIComponent(state.room.code)}/files`);
    if (state.ownerToken) request.setRequestHeader('X-Room-Owner-Key', state.ownerToken);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) status(`Uploading ${file.name} — ${Math.round(event.loaded / event.total * 100)}%`);
    };
    request.onerror = () => reject(new Error('Upload lost connection to the Local Send host.'));
    request.onload = () => {
      let body = {};
      try { body = JSON.parse(request.responseText); } catch (_) { /* use a generic error below */ }
      if (request.status >= 200 && request.status < 300) resolve();
      else reject(new Error(body.error || `Upload failed (${request.status}).`));
    };
    const data = new FormData();
    data.append('file', file, file.name);
    request.send(data);
  });
}

async function uploadFiles() {
  const files = [...elements.fileInput.files];
  if (!files.length) return;
  elements.upload.disabled = true;
  try {
    for (let index = 0; index < files.length; index += 1) {
      status(`Preparing ${index + 1} of ${files.length}: ${files[index].name}`);
      await uploadOne(files[index]);
    }
    await refreshRoom();
    status(`${files.length} file${files.length === 1 ? '' : 's'} uploaded and ready to share.`);
  } catch (error) {
    status(error.message, true);
  } finally {
    elements.fileInput.value = '';
    elements.upload.disabled = true;
  }
}

async function refreshRoom() {
  if (!state.room) return;
  try {
    const room = await api(`/api/rooms/${encodeURIComponent(state.room.code)}`);
    state.room = room;
    renderFiles(room.files);
  } catch (error) {
    status(error.message, true);
  }
}

async function deleteFile(file, button) {
  if (!window.confirm(`Delete ${file.original_name}? This cannot be undone.`)) return;
  button.disabled = true;
  try {
    await api(`/api/files/${encodeURIComponent(file.id)}`, {
      method: 'DELETE',
      headers: { 'X-Room-Owner-Key': state.ownerToken },
    });
    await refreshRoom();
    status(`${file.original_name} was deleted.`);
  } catch (error) {
    button.disabled = false;
    status(error.message, true);
  }
}

async function saveRoomMode() {
  elements.saveRoomMode.disabled = true;
  try {
    const room = await api(`/api/rooms/${encodeURIComponent(state.room.code)}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'X-Room-Owner-Key': state.ownerToken,
      },
      body: JSON.stringify({ access_mode: elements.roomMode.value }),
    });
    showRoom(room, state.ownerToken);
    status(room.access_mode === 'collaborative' ? 'Room updated: everyone can upload.' : 'Room updated: only you can upload.');
  } catch (error) {
    status(error.message, true);
  } finally {
    elements.saveRoomMode.disabled = false;
  }
}

async function deleteRoom() {
  if (!window.confirm(`Delete room ${state.room.code} and all of its files? This cannot be undone.`)) return;
  elements.deleteRoom.disabled = true;
  try {
    await api(`/api/rooms/${encodeURIComponent(state.room.code)}`, {
      method: 'DELETE',
      headers: { 'X-Room-Owner-Key': state.ownerToken },
    });
    showHome();
    status('Room and its files were deleted.');
  } catch (error) {
    status(error.message, true);
    elements.deleteRoom.disabled = false;
  }
}

async function downloadFile(file, button) {
  button.disabled = true;
  const original = button.textContent;
  try {
    const response = await fetch(`/api/files/${encodeURIComponent(file.id)}/download`);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Download failed (${response.status}).`);
    }
    const total = Number(response.headers.get('content-length')) || file.size_bytes;
    const reader = response.body.getReader();
    const chunks = [];
    let received = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      const percent = total ? ` — ${Math.round(received / total * 100)}%` : '';
      button.textContent = `Downloading${percent}`;
    }
    const url = URL.createObjectURL(new Blob(chunks));
    const link = document.createElement('a');
    link.href = url;
    link.download = file.original_name;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    button.textContent = 'Downloaded';
    setTimeout(() => { button.textContent = original; button.disabled = false; }, 1200);
  } catch (error) {
    button.textContent = original;
    button.disabled = false;
    status(error.message || 'Download could not be completed.', true);
  }
}

async function copyInvite() {
  try {
    if (!state.inviteBaseUrl) {
      const info = await api('/api/server-info');
      state.inviteBaseUrl = info.addresses[0] || window.location.origin;
    }
    const link = `${state.inviteBaseUrl}/#room=${state.room.code}`;
    await navigator.clipboard.writeText(link);
    status('Invite link copied. Share it with someone on the same Wi‑Fi.');
  } catch (_) {
    status(`Room code: ${state.room.code}. Share this code with people on the same Wi‑Fi.`);
  }
}

async function loadAddressNote() {
  try {
    const info = await api('/api/server-info');
    if (info.addresses.length) {
      state.inviteBaseUrl = info.addresses[0];
      elements.addressNote.replaceChildren('To join from another device, open ');
      const address = document.createElement('code');
      address.textContent = info.addresses[0];
      elements.addressNote.append(address, ' on the same Wi‑Fi. This transfer never uses the internet.');
      elements.addressNote.hidden = false;
    }
  } catch (_) {
    // The home screen remains usable; requests will show a clearer error if the host disappears.
  }
}

elements.create.addEventListener('click', createRoom);
elements.joinForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  try { await loadRoom(elements.codeInput.value); }
  catch (error) { status(error.message, true); }
});
elements.codeInput.addEventListener('input', () => { elements.codeInput.value = cleanCode(elements.codeInput.value); });
elements.fileInput.addEventListener('change', () => { elements.upload.disabled = !elements.fileInput.files.length; });
elements.upload.addEventListener('click', uploadFiles);
elements.saveRoomMode.addEventListener('click', saveRoomMode);
elements.deleteRoom.addEventListener('click', deleteRoom);
document.querySelector('#back-button').addEventListener('click', showHome);
document.querySelector('#refresh-button').addEventListener('click', refreshRoom);
document.querySelector('#copy-link-button').addEventListener('click', copyInvite);
document.querySelector('.brand').addEventListener('click', showHome);

const initialCode = new URLSearchParams(window.location.hash.slice(1)).get('room');
if (initialCode) loadRoom(initialCode).catch((error) => { status(error.message, true); });
loadAddressNote();
