/**
 * The vite dev server serves `/opt/appdata/` at the root `/` path.
 * So filenames beginning with that path need to be adjusted.
 *
 * @param  {string} filepath
 * @return {string}
 */
export function publicUrl(filepath) {
  if (filepath.startsWith('/opt/appdata/')) {
    //return filepath.replace('/opt/appdata/', 'http://localhost:9000/')
    return filepath.replace('/opt/appdata/', 'http://35.238.129.2:9000/')
  }
  return filepath;
}

/**
 * convert counts of pixels to acres
 */
export function toAcres(count) {
  if (!parseInt(count)) {
    return '';
  }
  const acres = (count * 30 * 30) / 4047; // square-meters to acres
  return acres.toFixed(1);
}
