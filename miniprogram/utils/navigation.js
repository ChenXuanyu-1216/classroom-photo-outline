function openWorkspace(platform, stack, page, replace = false) {
  const route = 'pages/upload/upload';
  for (let index = stack.length - 1; index >= 0; index--) {
    if (stack[index].route === route) {
      if (typeof stack[index].selectWorkspace === 'function') stack[index].selectWorkspace(page);
      const delta = stack.length - index - 1;
      if (delta) platform.navigateBack({ delta });
      return;
    }
  }
  const options = { url: '/' + route + (page === 'history' ? '?view=history' : '') };
  if (replace) platform.redirectTo(options);
  else platform.navigateTo(options);
}
module.exports = { openWorkspace };
