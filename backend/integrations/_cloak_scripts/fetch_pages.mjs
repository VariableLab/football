import { launch } from '/Users/liuxuran/Github/node_modules/cloakbrowser/dist/index.js';

const urls = JSON.parse(process.argv[2]);
const timeout = parseInt(process.argv[3] || '30000');

async function fetchPages() {
  const browser = await launch({ headless: true, ignoreHTTPSErrors: true });
  const results = [];

  for (const urlConfig of urls) {
    const { url, waitSelector, waitMs } = urlConfig;
    const start = Date.now();
    const page = await browser.newPage();

    try {
      await page.setDefaultTimeout(timeout);
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout });

      if (waitSelector) {
        await page.waitForSelector(waitSelector, { timeout: timeout });
      }
      if (waitMs) {
        await new Promise(r => setTimeout(r, waitMs));
      }

      const html = await page.content();
      const title = await page.title();
      const elapsed = Date.now() - start;

      results.push({
        url,
        html,
        title,
        statusCode: 200,
        elapsedMs: elapsed,
      });
    } catch (err) {
      results.push({
        url,
        html: '',
        title: '',
        statusCode: 0,
        error: err.message,
        elapsedMs: Date.now() - start,
      });
    } finally {
      await page.close();
    }
  }

  await browser.close();

  process.stdout.write(JSON.stringify(results));
}

fetchPages().catch(err => {
  process.stderr.write(err.message);
  process.exit(1);
});
