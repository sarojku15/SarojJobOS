(() => {
  const jobs = [...document.querySelectorAll('a[href*="/job-listings-"]')]
    .map(a => ({
      title: a.innerText.trim(),
      url: a.href
    }))
    .filter(j => j.title && j.url);

  const unique = [...new Map(jobs.map(j => [j.url, j])).values()];

  copy(JSON.stringify({
    source: "NAUKRI",
    captured_at: new Date().toISOString(),
    jobs: unique
  }, null, 2));

  console.log(`Captured ${unique.length} unique Naukri jobs.`);
  console.log("JSON copied to clipboard.");
})();
