const usernameInput = document.getElementById('username');
const statusDiv = document.getElementById('username-status');
const signupButton = document.getElementById('signup-button');
const messageDiv = document.getElementById('message');
let checkTimer = null;

usernameInput.addEventListener('input', function () {
  const username = usernameInput.value.trim();
  statusDiv.textContent = '';
  signupButton.disabled = false;
  if (username.length < 3) {
    return;
  }
  clearTimeout(checkTimer);
  checkTimer = setTimeout(function () {
    fetch('/api/v1/auth/check-username?username=' + encodeURIComponent(username))
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.available) {
          statusDiv.textContent = 'Username available';
          statusDiv.className = 'available';
          signupButton.disabled = false;
        } else {
          statusDiv.textContent = 'Username taken';
          statusDiv.className = 'taken';
          signupButton.disabled = true;
        }
      });
  }, 300);
});

document.getElementById('signup-form').addEventListener('submit', function (event) {
  event.preventDefault();
  messageDiv.textContent = '';
  const payload = {
    username: usernameInput.value.trim(),
    email: document.getElementById('email').value.trim(),
    password: document.getElementById('password').value,
  };
  fetch('/api/v1/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
    .then(function (res) {
      return res.json().then(function (data) {
        return { ok: res.ok, data: data };
      });
    })
    .then(function (result) {
      if (result.ok) {
        messageDiv.textContent = 'Signup successful, welcome ' + result.data.username;
        messageDiv.style.color = 'green';
      } else {
        messageDiv.textContent = result.data.detail || 'Signup failed';
        messageDiv.style.color = 'red';
      }
    });
});
