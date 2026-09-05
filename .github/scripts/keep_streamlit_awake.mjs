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

  // A real Streamlit page has this container after it wakes and connects.
  await page.locator('[data-testid="stAppViewContainer"]').waitFor({
    state: "visible",
    timeout: 90_000,
  });

  const bodyText = await page.locator("body").innerText();
  if (/this app has gone to sleep|yes, get this app back up/i.test(bodyText)) {
    throw new Error("The app is still showing the Streamlit sleep screen.");
  }

  console.log("Streamlit app loaded successfully.");
} finally {
  await browser.close();
}
