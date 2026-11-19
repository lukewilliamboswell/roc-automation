// Browser audit support; Chrome stderr is a stream, not a sequence of lines.
export function waitForDevtools(child, milliseconds = 60000) {
  return new Promise((resolve, reject) => {
    let output = '';
    let pending = '';
    let settled = false;
    const settle = (error, endpoint) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (error) reject(new Error(`Chrome did not expose a debugging port: ${error}\n${output.slice(-8192)}`));
      else resolve(endpoint);
    };
    const timeout = setTimeout(() => settle(`no endpoint within ${milliseconds / 1000} s`), milliseconds);
    child.stderr.setEncoding('utf8');
    // Retain the listener after success to keep draining the child's pipe.
    child.stderr.on('data', chunk => {
      if (settled) return;
      output = (output + chunk).slice(-65536);
      pending += chunk;
      let newline;
      while ((newline = pending.indexOf('\n')) !== -1) {
        const line = pending.slice(0, newline).replace(/\r$/, '');
        pending = pending.slice(newline + 1);
        const match = /^DevTools listening on (ws:\/\/\S+)$/.exec(line);
        if (!match) continue;
        try {
          const endpoint = new URL(match[1]);
          if (!endpoint.port) throw new Error('endpoint has no port');
          settle(null, endpoint);
        } catch (error) { settle(`invalid endpoint: ${error.message}`); }
        return;
      }
      if (pending.length > 65536) settle('stderr line exceeds 64 KiB');
    });
    child.once('error', error => settle(error.message));
    child.once('exit', (code, signal) => settle(`Chrome exited (${signal ?? `code ${code}`})`));
  });
}
