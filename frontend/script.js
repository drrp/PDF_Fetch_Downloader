const searchBtn = document.getElementById("search-btn");
const queryInput = document.getElementById("query");
const chkSvk = document.getElementById("chk-svk");
const chkIa = document.getElementById("chk-ia");
const chkManasu = document.getElementById("chk-manasu");
const chkTtd = document.getElementById("chk-ttd");
const resultsContainer = document.getElementById("results");
const statTotal = document.getElementById("stat-total");
const resultsCountLabel = document.getElementById("results-count-label");

let allSearchResults = []; // మొత్తం ఫలితాలు స్టోర్ చేసుకోవడానికి
let currentPage = 1;
const itemsPerPage = 10;

async function performSearch(page = 1) {
  currentPage = page;

  // ఒకవేళ మొదటిసారి సెర్చ్ చేస్తుంటే లేదా క్వెరీ మారితేనే సర్వర్‌కి రిక్వెస్ట్ వెళ్తుంది
  if (page === 1 || allSearchResults.length === 0) {
    const query = queryInput.value.trim();
    const useSvk = chkSvk.checked;
    const useIa = chkIa.checked;
    const useManasu = chkManasu.checked;
    const useTtd = chkTtd.checked;

    if (!query) {
      Swal.fire({
        title: "గమనిక",
        text: "దయచేసి శోధన పదాన్ని నమోదు చేయండి.",
        icon: "warning",
        confirmButtonColor: "#1e3a8a",
      });
      return;
    }

    resultsContainer.innerHTML = `
        <div class="status-message">
            <div class="spinner"></div>
            ఎంచుకున్న లైబ్రరీల నుండి పుస్తకాలను వెతుకుతోంది...
        </div>
    `;
    searchBtn.disabled = true;

    try {
      // లిమిట్ లేకుండా అన్ని రిజల్ట్స్ ఒకేసారి తెచ్చుకోవడం కోసం 
      const response = await fetch(
        `/search?query=${encodeURIComponent(query)}&use_svk=${useSvk}&use_ia=${useIa}&use_manasu=${useManasu}&use_ttd=${useTtd}&limit=500`
      );
      const data = await response.json();

      allSearchResults = data.results || [];
      statTotal.innerText = allSearchResults.length;
    } catch (error) {
      resultsContainer.innerHTML =
        '<div class="status-message" style="color: #dc2626;">సమాచారాన్ని పొందడంలో లోపం ఏర్పడింది.</div>';
      searchBtn.disabled = false;
      return;
    } finally {
      searchBtn.disabled = false;
    }
  }

  // లోకల్ డేటా నుండే పేజీకి సరిపడా 10 ఐటమ్స్ కట్ చేసి చూపించడం (ఎలాంటి లోడింగ్ ఉండదు)
  renderCurrentPageResults();
}

function renderCurrentPageResults() {
  resultsContainer.innerHTML = "";

  const totalBooks = allSearchResults.length;
  const totalPages = Math.ceil(totalBooks / itemsPerPage) || 1;

  resultsCountLabel.innerText = `మొత్తం: ${totalBooks} పుస్తకాలు (పేజీ ${currentPage} / ${totalPages})`;

  if (totalBooks === 0) {
    resultsContainer.innerHTML =
      '<div class="status-message">ఈ శోధనకు ఎలాంటి పుస్తకాలు లభించలేదు.</div>';
    return;
  }

  const startIdx = (currentPage - 1) * itemsPerPage;
  const endIdx = startIdx + itemsPerPage;
  const pageItems = allSearchResults.slice(startIdx, endIdx);

 pageItems.forEach((book) => {
      const card = document.createElement("div");
      card.className = "book-card";

      const coverHtml = book.cover_image
        ? `<img src="${book.cover_image}" alt="Cover" class="book-cover" onerror="this.outerHTML='<div class=\\'no-cover\\'>ప్రతి లేదు</div>'">`
        : `<div class="no-cover">ప్రతి లేదు</div>`;

      let badgeClass = "badge-svk";
      if (book.source === "Internet Archive") badgeClass = "badge-ia";
      if (book.source === "Manasu Foundation") badgeClass = "badge-manasu";
      if (book.source === "TTD Ebooks") badgeClass = "badge-ttd";

      // కేవలం టైటిల్, రచయిత, సంవత్సరం మాత్రమే (వివరణ తొలగించబడింది)
      let authorHtml = book.author ? `<div class="book-author"><i class="fa-solid fa-pen-nib"></i> ${book.author}</div>` : '';
      let yearHtml = book.year ? `<span class="book-year">(${book.year})</span>` : '';

      card.innerHTML = `
              <div class="book-info">
                  ${coverHtml}
                  <div class="book-details-content">
                      <span class="badge ${badgeClass}">${book.source}</span>
                      <div class="book-title">${book.title} ${yearHtml}</div>
                      ${authorHtml}
                  </div>
              </div>
              <button class="action-btn" onclick="savePdfLocally('${book.download_url}', '${book.title.replace(/'/g, "\\'")}', '${book.source}', this)">
                  <i class="fa-solid fa-download"></i> సేవ్ చేయి
              </button>
          `;
      resultsContainer.appendChild(card);
    });

  if (totalPages > 1) {
    renderPagination(currentPage, totalPages);
  }
}

// పేజినేషన్ బటన్స్ కోసం (ఇది పేజీ మారినప్పుడు కేవలం లోకల్ డేటాని మాత్రమే మారుస్తుంది)
function renderPagination(currentPage, totalPages) {
  const paginationDiv = document.createElement("div");
  paginationDiv.className = "pagination-container";
  
  let paginationHtml = `<button class="page-btn" ${currentPage === 1 ? 'disabled' : ''} onclick="changePage(${currentPage - 1})"><i class="fa-solid fa-chevron-left"></i> వెనుకకు</button>`;
  
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= currentPage - 2 && i <= currentPage + 2)) {
      paginationHtml += `<button class="page-btn ${i === currentPage ? 'active' : ''}" onclick="changePage(${i})">${i}</button>`;
    } else if (i === currentPage - 3 || i === currentPage + 3) {
      paginationHtml += `<span style="padding: 8px; color: #64748b;">...</span>`;
    }
  }

  paginationHtml += `<button class="page-btn" ${currentPage === totalPages ? 'disabled' : ''} onclick="changePage(${currentPage + 1})">తదుపరి <i class="fa-solid fa-chevron-right"></i></button>`;
  
  paginationDiv.innerHTML = paginationHtml;
  resultsContainer.appendChild(paginationDiv);
}

function changePage(page) {
  currentPage = page;
  renderCurrentPageResults();
  window.scrollTo({ top: 0, behavior: 'smooth' }); // పేజీ మారగానే పైకి స్క్రోల్ అవుతుంది
}

async function savePdfLocally(url, title, source, btnElement) {
  Swal.fire({
    title: "పుస్తకం డౌన్‌లోడ్ అవుతోంది",
    html: `
          <div style="margin: 20px 0; text-align: center;">
              <p style="color: #0f172a; font-size: 15px; font-weight: 600; margin-bottom: 15px; line-height: 1.4;">"${title}"</p>
              
              <div id="progress-bar-container" style="background: #e2e8f0; border-radius: 6px; overflow: hidden; height: 24px; padding: 2px; margin-bottom: 12px; box-shadow: inset 0 1px 2px rgba(0,0,0,0.1);">
                  <div id="progress-bar-fill" style="background: linear-gradient(90deg, #2563eb, #1d4ed8); width: 5%; height: 100%; border-radius: 4px; transition: width 0.4s ease;"></div>
              </div>
              
              <div style="display: flex; justify-content: space-between; font-size: 13px; font-weight: 600; color: #475569;">
                  <span id="progress-percent">5%</span>
                  <span id="progress-text">సర్వర్‌‌తో కనెక్ట్ అవుతోంది...</span>
              </div>
          </div>
      `,
    allowOutsideClick: false,
    allowEscapeKey: false,
    showConfirmButton: false,
    didOpen: () => {
      const fill = document.getElementById("progress-bar-fill");
      const percent = document.getElementById("progress-percent");
      const text = document.getElementById("progress-text");

      let currentPercent = 5;

      window.downloadProgressInterval = setInterval(() => {
        if (currentPercent < 90) {
          const increment =
            currentPercent < 40
              ? Math.floor(Math.random() * 8) + 5
              : Math.floor(Math.random() * 3) + 1;
          currentPercent = Math.min(currentPercent + increment, 90);

          if (fill) fill.style.width = currentPercent + "%";
          if (percent) percent.innerText = currentPercent + "%";

          if (currentPercent > 60 && text) {
            text.innerText = "ఫైల్ స్ట్రీమ్ ప్రాసెస్ చేయబడుతోంది...";
          } else if (currentPercent > 30 && text) {
            text.innerText = "లైబ్రరీ నుండి డేటా సేకరించబడుతోంది...";
          }
        }
      }, 400);
    },
    willClose: () => {
      clearInterval(window.downloadProgressInterval);
    },
  });

  try {
    const response = await fetch(
      `/download-book?book_page_url=${encodeURIComponent(url)}&title=${encodeURIComponent(title)}&source=${encodeURIComponent(source)}`,
      {
        signal: window.currentFetchController
          ? window.currentFetchController.signal
          : undefined,
      },
    );
    const data = await response.json();

    clearInterval(window.downloadProgressInterval);
    const fill = document.getElementById("progress-bar-fill");
    const percent = document.getElementById("progress-percent");
    const text = document.getElementById("progress-text");
    if (fill) fill.style.width = "100%";
    if (percent) percent.innerText = "100%";
    if (text) text.innerText = "పూర్తయింది!";

    await new Promise((r) => setTimeout(r, 400));

    if (data.status === "success") {
      btnElement.innerHTML = '<i class="fa-solid fa-check"></i> సేవ్ అయింది';
      btnElement.style.backgroundColor = "#1e3a8a";

      Swal.fire({
        title: "డౌన్‌లోడ్ పూర్తయింది!",
        text: `"${title}" విజయవంతంగా మీ కంప్యూటర్‌లో సేవ్ చేయబడింది.`,
        icon: "success",
        showCancelButton: true,
        confirmButtonColor: "#059669",
        cancelButtonColor: "#64748b",
        confirmButtonText:
          '<i class="fa-solid fa-book-open"></i> పుస్తకం తెరవండి',
        cancelButtonText: "మూసివేయండి",
      }).then((res) => {
        if (res.isConfirmed) {
          window.open(data.file_url, "_blank");
        }
      });
    } else if (data.status === "gdrive_restricted") {
      btnElement.innerHTML =
        '<i class="fa-brands fa-google-drive"></i> డ్రైవ్‌లో తెరవండి';
      btnElement.style.backgroundColor = "#d97706";

      Swal.fire({
        title: "గూగుల్ లాగిన్ అవసరం",
        text: "ఈ పుస్తకానికి Google ఖాతా అనుమతి అవసరం. దయచేసి దీన్ని నేరుగా డ్రైవ్‌లో తెరవండి.",
        icon: "info",
        showCancelButton: true,
        confirmButtonColor: "#2563eb",
        cancelButtonColor: "#64748b",
        confirmButtonText:
          '<i class="fa-solid fa-arrow-up-right-from-square"></i> డ్రైవ్‌లో తెరవండి',
        cancelButtonText: "మూసివేయండి",
      }).then((res) => {
        if (res.isConfirmed) {
          window.open(data.url, "_blank");
        }
      });
    } else {
      Swal.fire({
        title: "విఫలమైంది",
        text: data.message,
        icon: "error",
        confirmButtonColor: "#dc2626",
      });
    }
  } catch (error) {
    if (error.name === "AbortError") return;

    clearInterval(window.downloadProgressInterval);
    Swal.fire({
      title: "లోపం",
      text: "సర్వర్‌తో కనెక్షన్ విఫలమైంది.",
      icon: "error",
      confirmButtonColor: "#dc2626",
    });
  }
}

let currentFetchController = null;
let downloadProgressInterval = null;
let searchRequestSequence = 0;

function confirmAppReset() {
  Swal.fire({
    title: "అప్లికేషన్‌ను రీసెట్ చేయాలా?",
    text: "అన్ని ఆక్టివ్ డౌన్‌లోడ్‌లు, శోధనలు నిలిపివేయబడతాయి మరియు సెట్టింగ్‌లు రీసెట్ చేయబడతాయి.",
    icon: "warning",
    showCancelButton: true,
    confirmButtonColor: "#dc2626",
    cancelButtonColor: "#64748b",
    confirmButtonText: "అవును, రీసెట్ చేయి",
    cancelButtonText: "రద్దు చేయు",
  }).then(async (result) => {
    if (result.isConfirmed) {
      searchRequestSequence++;
      if (downloadProgressInterval) {
        clearInterval(downloadProgressInterval);
      }
      if (currentFetchController) {
        currentFetchController.abort();
      }

      try {
        await fetch("/reset-app", { method: "POST" });
      } catch (e) {
        console.error("Backend reset notice failed", e);
      }

      Swal.fire({
        title: "రీసెట్ చేయబడింది!",
        text: "అప్లికేషన్ విజయవంతంగా పునఃప్రారంభించబడుతోంది...",
        icon: "success",
        timer: 1200,
        showConfirmButton: false,
      }).then(() => {
        // పేజీని ఆటోమేటిక్‌గా రీలోడ్ చేయడం కోసం
        location.reload();
      });
    }
  });
}

queryInput.addEventListener("keypress", (e) => {
  if (e.key === "Enter") performSearch();
});
