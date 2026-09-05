import { chromium } from "playwright";

const appUrl = process.env.STREAMLIT_APP_URL?.trim().replace(/\/$/, "");

if (!appUrl) {
  throw new Error("STREAMLIT_APP_URL is not configured.");
}

if (!/^https:\/\//i.test(appUrl)) {
  throw new Error("STREAMLIT_APP_URL must be an HTTPS URL.");
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
});

try {
  console.log(`Opening ${appUrl}`);
  await page.goto(appUrl, {
    waitUntil: "domcontentloaded",
    timeout: 60_000,
  });

  // Give Streamlit time to render either the app or its sleep screen.
  await page.waitForTimeout(5_000);

  if (/share\.streamlit\.io\/.+auth|\/\-\/login/i.test(page.url())) {
    throw new Error(
      `The app redirected to an authentication page (${page.url()}). ` +
        "The GitHub runner needs anonymous access to the app."
    );
  }

  const wakeButton = page.getByRole("button", {
    name: /yes, get this app back up/i,
  });

  if (await wakeButton.count()) {
    console.log("The app is sleeping; clicking the wake-up button.");
    await wakeButton.first().click({ timeout: 15_000 });
  } else {
    console.log("The app is not showing the sleep screen.");
  }

  // Cold starts can take several minutes while Streamlit rebuilds the app.
  // Target the actual heading so a partially rendered shell is not mistaken
  // for a ready app.
  await page.locator("h1").filter({
    hasText: /Assimil French Anki Generator/,
  }).first().waitFor({
    state: "visible",
    timeout: 300_000,
  });

  const bodyText = await page.locator("body").innerText();
  if (/this app has gone to sleep|yes, get this app back up/i.test(bodyText)) {
    throw new Error("The app is still showing the Streamlit sleep screen.");
  }

  console.log("Streamlit app loaded successfully.");
} catch (error) {
  console.error(`Final browser URL: ${page.url()}`);
  try {
    console.error("Page text:");
    console.error((await page.locator("body").innerText()).slice(0, 4000));
    await page.screenshot({ path: "streamlit-debug.png", fullPage: true });
  } catch (diagnosticError) {
    console.error(`Could not collect browser diagnostics: ${diagnosticError}`);
  }
  throw error;
} finally {
  await browser.close();
}
