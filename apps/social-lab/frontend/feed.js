let currentUsername = '';


let nextCursor = null;
let feedMode = 'all';
let followingSet = {};

function updateLoadMoreButton() {
  var button = document.getElementById('load-more-button');
  if (button) {
    button.hidden = !nextCursor;
  }
}

function loadFollowingSet() {
  fetch('/api/v1/following', { credentials: 'same-origin' })
    .then(function (res) { return res.ok ? res.json() : { following: [] }; })
    .then(function (data) {
      followingSet = {};
      data.following.forEach(function (u) { followingSet[u] = true; });
      loadFeed(true);
    })
    .catch(function () { followingSet = {}; loadFeed(true); });
}

function loadFeed(incrementViews) {
  var feedUrl = feedMode === 'following' ? '/api/v1/feed/following' : '/api/v1/feed';
  fetch(feedUrl, { credentials: 'same-origin' })
    .then(function (res) { return res.json(); })
    .then(function (data) {
      const postsDiv = document.getElementById('posts');
      postsDiv.textContent = '';
      var posts = Array.isArray(data) ? data : data.posts;
      nextCursor = Array.isArray(data) ? null : data.next_cursor;
      if (posts.length === 0) {
        postsDiv.textContent = 'No posts yet';
      } else {
        posts.forEach(function (post) {
          renderPost(post, postsDiv, incrementViews);
        });
      }
      updateLoadMoreButton();
    });
}

function loadMore() {
  if (!nextCursor) {
    return;
  }
  var moreBase = feedMode === 'following' ? '/api/v1/feed/following' : '/api/v1/feed';
  fetch(moreBase + '?cursor=' + encodeURIComponent(nextCursor), { credentials: 'same-origin' })
    .then(function (res) { return res.json(); })
    .then(function (data) {
      const postsDiv = document.getElementById('posts');
      var posts = Array.isArray(data) ? data : data.posts;
      nextCursor = Array.isArray(data) ? null : data.next_cursor;
      posts.forEach(function (post) {
        renderPost(post, postsDiv, true);
      });
      updateLoadMoreButton();
    });
}

function updatePostMeta(post) {
  var el = document.getElementById('post-' + post.id);
  if (!el) { return; }
  var meta = el.querySelector('.post-meta');
  if (!meta) { return; }
  var uniqueText = '';
  if (post.unique_views !== null && post.unique_views !== undefined) {
    uniqueText = ' | ' + post.unique_views + ' unique views';
  } else {
    var m = meta.textContent.match(/ \| [0-9]+ unique views/);
    if (m) { uniqueText = m[0]; }
  }
  meta.textContent = post.likes_count + ' likes | ' + post.views + ' views' + uniqueText + ' | ' + post.comments.length + ' comments';
}

function renderPost(post, postsDiv, incrementViews) {
  const postDiv = document.createElement('div');
  postDiv.className = 'post';
  postDiv.id = 'post-' + post.id;

  const authorDiv = document.createElement('div');
  authorDiv.className = 'post-author';
  authorDiv.textContent = post.username;
  postDiv.appendChild(authorDiv);

  const timeDiv = document.createElement('div');
  timeDiv.className = 'post-time';
  timeDiv.textContent = post.created_at || '';
  postDiv.appendChild(timeDiv);

  const contentP = document.createElement('p');
  contentP.textContent = post.content;
  postDiv.appendChild(contentP);

  const metaDiv = document.createElement('div');
  metaDiv.className = 'post-meta';
  var uniqueText = (post.unique_views !== null && post.unique_views !== undefined) ? (' | ' + post.unique_views + ' unique views') : '';
  metaDiv.textContent = post.likes_count + ' likes | ' + post.views + ' views' + uniqueText + ' | ' + post.comments.length + ' comments';
  postDiv.appendChild(metaDiv);

  const actionsDiv = document.createElement('div');
  actionsDiv.className = 'post-actions';
  const likeButton = document.createElement('button');
  likeButton.textContent = 'Like';
  likeButton.addEventListener('click', function () {
    fetch('/api/v1/posts/' + post.id + '/like', {
      method: 'POST',
      headers: csrfHeaders(),
      credentials: 'same-origin',
      body: JSON.stringify({}),
    }).then(function (res) { return res.ok ? res.json() : null; }).then(function (updated) { if (updated) { updatePostMeta(updated); } });
  });
  actionsDiv.appendChild(likeButton);
  if (post.username !== currentUsername) {
    const followButton = document.createElement('button');
    followButton.textContent = followingSet[post.username] ? 'Unfollow' : 'Follow';
    followButton.addEventListener('click', function () {
      var action = followingSet[post.username] ? 'unfollow' : 'follow';
      fetch('/api/v1/users/' + post.username + '/' + action, {
        method: 'POST',
        headers: csrfHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({}),
      }).then(function () {
        var wasFollowing = followingSet[post.username];
        followingSet[post.username] = !wasFollowing;
        followButton.textContent = followingSet[post.username] ? 'Unfollow' : 'Follow';
        if (feedMode === 'following' && !followingSet[post.username]) {
          var allPosts = postsDiv.querySelectorAll('.post');
          allPosts.forEach(function (el) {
            var a = el.querySelector('.post-author');
            if (a && a.textContent === post.username) { el.remove(); }
          });
        }
      });
    });
    actionsDiv.appendChild(followButton);
  }
  if (post.username === currentUsername) {
    const deleteButton = document.createElement('button');
    deleteButton.textContent = 'Delete';
    deleteButton.addEventListener('click', function () {
      fetch('/api/v1/posts/' + post.id, {
        method: 'DELETE',
        headers: csrfHeaders(),
        credentials: 'same-origin',
      }).then(function () { var el = document.getElementById('post-' + post.id); if (el) { el.remove(); } });
    });
    actionsDiv.appendChild(deleteButton);
  }
  postDiv.appendChild(actionsDiv);

  const commentsDiv = document.createElement('div');
  commentsDiv.className = 'post-comments';
  post.comments.forEach(function (comment) {
    const commentP = document.createElement('p');
    commentP.textContent = comment.username + ': ' + comment.text;
    commentsDiv.appendChild(commentP);
  });
  postDiv.appendChild(commentsDiv);

  const commentInput = document.createElement('input');
  commentInput.type = 'text';
  commentInput.placeholder = 'Write a comment';
  commentInput.className = 'comment-input';
  postDiv.appendChild(commentInput);

  const commentButton = document.createElement('button');
  commentButton.textContent = 'Comment';
  commentButton.addEventListener('click', function () {
    const text = commentInput.value.trim();
    if (!text) {
      alert('Please write a comment first');
      return;
    }
    fetch('/api/v1/posts/' + post.id + '/comment', {
      method: 'POST',
      headers: csrfHeaders(),
      credentials: 'same-origin',
      body: JSON.stringify({ text: text }),
    }).then(function (res) { return res.ok ? res.json() : null; }).then(function (updated) {
      if (!updated) { return; }
      updatePostMeta(updated);
      var box = document.getElementById('post-' + post.id).querySelector('.post-comments');
      var commentP = document.createElement('p');
      commentP.textContent = currentUsername + ': ' + text;
      box.appendChild(commentP);
      commentInput.value = '';
    });
  });
  postDiv.appendChild(commentButton);

  postsDiv.appendChild(postDiv);

  if (incrementViews) {
    fetch('/api/v1/posts/' + post.id + '/view', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': getCsrfToken() } });
  }
}

document.getElementById('create-post-form').addEventListener('submit', function (event) {
  event.preventDefault();
  const messageDiv = document.getElementById('message');
  messageDiv.textContent = '';
  const payload = {
    content: document.getElementById('post-content').value.trim(),
  };
  fetch('/api/v1/posts', {
    method: 'POST',
    headers: csrfHeaders(),
    credentials: 'same-origin',
    body: JSON.stringify(payload),
  })
    .then(function (res) { return res.json(); })
    .then(function (data) {
      if (data.id) {
        messageDiv.textContent = 'Post created';
        messageDiv.style.color = 'green';
        document.getElementById('post-content').value = '';
        loadFeed(false);
      } else {
        messageDiv.textContent = data.detail || 'Post failed';
        messageDiv.style.color = 'red';
      }
    });
});

document.getElementById('logout-button').addEventListener('click', function () {
  fetch('/api/v1/auth/logout', { headers: { 'X-CSRF-Token': getCsrfToken() },
    method: 'POST',
    credentials: 'same-origin',
  }).finally(function () {
    localStorage.removeItem('social_lab_token');
    localStorage.removeItem('social_lab_username');
    window.location.href = '/static/login.html';
  });
});

(function initFeed() {
  fetch('/api/v1/auth/me', {
    credentials: 'same-origin',
  })
    .then(function (res) {
      if (res.ok) {
        return res.json();
      }
      return fetch('/api/v1/auth/refresh', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': getCsrfToken() }
      }).then(function (refreshRes) {
        if (!refreshRes.ok) {
          throw new Error('invalid session');
        }
        return fetch('/api/v1/auth/me', {
          credentials: 'same-origin',
        }).then(function (res2) {
          if (!res2.ok) {
            throw new Error('invalid session');
          }
          return res2.json();
        });
      });
    })
    .then(function (data) {
      currentUsername = data.username;
      document.getElementById('logged-in-user').textContent = 'Logged in as ' + currentUsername;
      loadFollowingSet();
    })
    .catch(function () {
      localStorage.removeItem('social_lab_token');
      localStorage.removeItem('social_lab_username');
      window.location.href = '/static/login.html';
    });
})();

function getCsrfToken() {
  var parts = document.cookie.split(';');
  for (var i = 0; i < parts.length; i++) {
    var part = parts[i].trim();
    if (part.indexOf('csrf_token=') === 0) {
      return part.substring('csrf_token='.length);
    }
  }
  return '';
}

function csrfHeaders() {
  return { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() };
}

document.getElementById('load-more-button').addEventListener('click', loadMore);
document.getElementById('all-feed-button').addEventListener('click', function () { feedMode = 'all'; loadFeed(true); });
document.getElementById('following-feed-button').addEventListener('click', function () { feedMode = 'following'; loadFeed(true); });
