document.getElementById('login-form').addEventListener('submit', function (event) {
  event.preventDefault();
  const messageDiv = document.getElementById('message');
  messageDiv.textContent = '';
  const payload = {
    username: document.getElementById('username').value.trim(),
    password: document.getElementById('password').value,
  };
  fetch('/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
    .then(function (res) {
      return res.json().then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    })
    .then(function (result) {
      if (result.ok) {
        messageDiv.textContent = 'Login successful, welcome ' + result.data.username;
        messageDiv.style.color = 'green';
      } else if (result.status === 429) {
        messageDiv.textContent = result.data.detail + ' (rate limit, try again after 60 seconds)';
        messageDiv.style.color = 'red';
      } else {
        messageDiv.textContent = result.data.detail || 'Login failed';
        messageDiv.style.color = 'red';
      }
    });
});
