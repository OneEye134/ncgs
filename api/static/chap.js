function checkschaps() {
    const schapsv = document.getElementById("schaps").value;
    
    if (schapsv == "OOpsi..."){
      LoadSecretChapter(1)
    } else if (schapsv == "AAAAHHHH") {
      LoadSecretChapter(2)
    } else if (schapsv == "The... RIDE!") {
      LoadSecretChapter(3)
    } else if (schapsv == "UxL1") {
      LoadSecretChapter(4)
    } else {
      alert("Invalid")
    }
  }
  
  function fixSpacing(text) {
    // Add space after .,?! if next char is a letter or quote and not already space
    return text.replace(/([.,?!])(["']?)(\w)/g, '$1$2 $3');
}

function renderChapterMarkdown(mdText) {
// Fix spacing issues
mdText = fixSpacing(mdText);
mdText = mdText.replace(/^\[(.+?)\](#+)\s*(.+)$/gm, (match, id, hashes, title) => {
    return `${hashes} ${title} {#${id}}`;
});
// Convert Markdown to HTML
return marked.parse(mdText, { headerIds: true });
}

function loadChapter() {
window.scrollTo({ top: 0, behavior: 'smooth' });
const select = document.getElementById('chapter-select');
const chapterNumber = parseInt(select.value);
const contentDiv = document.getElementById('chapter-content');
const prevButton = document.getElementById('prev-chap');
const nextButton = document.getElementById('next-chap');

if (!chapterNumber) {
    contentDiv.innerHTML = `
        <h3>WARNING!: Please read the WHOLE warning before continuing.</h3>
        <p>This novel is NOT about romance, it's about school friends going through random challenges of school.</p>
        <p>Just like I said, they are school friends! I don't care about what you think they are, but they are simply just friends.</p>
        <p>To all the classmates who have their names in the story, I only used your names because I don't know a lot of names, so I'm not making any of you look bad. I am only using your name for a different character.</p>
        <p>So if you think you were placed in the story, you are actually not in the story. It might be your name, but it is a whole different character.</p>     
        <h3 class="red">ULYSSES AND LEIGHTON ARE NOT IN A RELATIONSHIP!!!</h3>
        <h3 class="red">YOU MIGHT BE WONDERING WHY ULYSSES AND LEIGHTON APPEAR A LOT IN THE STORY, IT'S BECAUSE THE BOTH OF THEM ARE SIMPLY THE MAIN CHARACTERS, AND THEY MIGHT HAVE THE SAME NAME AS YOU, BUT I ASSURE YOU THAT THEY ARE WHOLE DIFFERENT CHARACTERS.</h3>
        <h4>Happy Reading! - NCGS Studios</h4>`;
    prevButton.disabled = true;
    nextButton.disabled = true;
    return;
}

// Show loading message
contentDiv.innerHTML = '<h1>Loading...</h1>';

const chapterFile = `/static/OUR.SCHOOL.LIFE/${chapterNumber}chap.md`;

fetch(chapterFile)
    .then(response => {
        if (!response.ok) throw new Error('Chapter not found');
        return response.text();
    })
    .then(mdText => {
        contentDiv.innerHTML = renderChapterMarkdown(mdText);
        const elements = contentDiv.querySelectorAll("*");

        elements.forEach(el => {
            const text = el.textContent.trim();
            // Check if it ends with {#something}
            const match = text.match(/\{#(.+?)\}$/);
            if (match) {
                const id = match[1];

                // Set it as actual element ID
                el.id = id;

                // Remove the {#...} from visible text
                el.textContent = text.replace(/\s*\{#.+?\}$/, "");
            }
        });
    })
    .catch(err => {
        contentDiv.innerHTML = `<p>Error: ${err.message}</p>`;
    });

// Enable/disable navigation buttons
prevButton.disabled = (chapterNumber === 1);
nextButton.disabled = (chapterNumber === 43);

// Dynamically add chapter-specific stylesheet if it's not already added
if (!document.getElementById('chap-style')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/chap-style.css';
    link.id = 'chap-style';
    document.head.appendChild(link);
}
}

function LoadSecretChapter(n) {
window.scrollTo({ top: 0, behavior: 'smooth' });
const contentDiv = document.getElementById('chapter-content');

// Show loading message
contentDiv.innerHTML = '<h1>Loading Secret Chapter...</h1>';

const secretChapterFile = `OUR.SCHOOL.LIFE/S${n}chap.html`;

fetch(secretChapterFile)
    .then(response => {
        if (!response.ok) {
            throw new Error('Secret chapter not found');
        }
        return response.text();
    })
    .then(data => {
        contentDiv.innerHTML = data;
    })
    .catch(err => {
        contentDiv.innerHTML = `<p>Error: ${err.message}</p>`;
    });

    if (!document.getElementById('chap-style')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'chap-style.css';
    link.id = 'chap-style';
    document.head.appendChild(link);
}
}
    function changeChapter(direction) {
        const select = document.getElementById('chapter-select');
        let currentChapter = parseInt(select.value) || 1;
        let newChapter = currentChapter + direction;

        if (newChapter >= 1 && newChapter <= 43) {
            select.value = newChapter;
            loadChapter();
        }
    }