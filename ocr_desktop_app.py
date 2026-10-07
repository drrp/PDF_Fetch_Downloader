import os
import fitz # PyMuPDF
import streamlit as st
from google import genai
from google.genai import types

# 🌟 జెమిని క్లైంట్ ఇనిషియలైజేషన్ (మీ API కీ ఇక్కడ సెట్ చేయండి లేదా ఎన్విరాన్‌‌మెంట్ వేరియబుల్ వాడండి)
# os.environ["GEMINI_API_KEY"] = "మీ_ఏపీఐ_కీ"
client = genai.Client()

LOCAL_DOWNLOAD_DIR = r"D:\Environment\pdf_book_puller\downloads"

st.set_page_config(page_title="తెలుగు గ్రంథాల OCR & RAG ఇంజిన్", layout="wide")

st.title("📚 స్వతంత్ర తెలుగు OCR & RAG అన్వేషణ ఇంజిన్")
st.markdown("లోకల్ డైరెక్టరీ నుండి PDFలను విశ్లేషించి, జెమిని AI సహాయంతో లోతైన శోధన మరియు విశ్లేషణ చేసే ప్రత్యేక ప్యానెల్.")

# సైడ్‌బార్‌లో లోకల్ ఫోల్డర్ స్టేటస్ మరియు ఫైల్స్
st.sidebar.header("📁 లోకల్ ఫోల్డర్ ఫైల్స్")
if os.path.exists(LOCAL_DOWNLOAD_DIR):
    pdf_files = [f for f in os.listdir(LOCAL_DOWNLOAD_DIR) if f.endswith('.pdf')]
    st.sidebar.success(f"మొత్తం PDFలు లభ్యం: {len(pdf_files)}")
    selected_pdf = st.sidebar.selectbox("పుస్తకాన్ని ఎంచుకోండి:", pdf_files)
else:
    st.sidebar.error("డౌన్‌లోడ్స్ ఫోల్డర్ కనుగొనబడలేదు!")

# మెయిన్ ట్యాబ్స్
tab1, tab2 = st.tabs(["🔍 RAG శోధన & విశ్లేషణ", "⚡ మాన్యువల్ OCR & ప్రివ్యూ"])

with tab1:
    st.subheader("పుస్తకాల లోపల సమగ్ర భావన శోధన (RAG Search)")
    query = st.text_input("ప్రశ్న లేదా పదం టైప్ చేయండి (ఉదా: సంధి రూపాలు, లక్షణములు):", "")
    
    if st.button("శోధించు (Search & RAG)", type="primary"):
        if query:
            with st.spinner("లోకల్ PDFల నుండి సమాచారాన్ని సేకరిస్తోంది..."):
                results = []
                # లోకల్ ఫోల్డర్‌లోని అన్ని PDFలలో కీవర్డ్ కోసం వెతకడం
                if os.path.exists(LOCAL_DOWNLOAD_DIR):
                    for f in os.listdir(LOCAL_DOWNLOAD_DIR):
                        if f.endswith('.pdf'):
                            path = os.path.join(LOCAL_DOWNLOAD_DIR, f)
                            try:
                                doc = fitz.open(path)
                                for page_num, page in enumerate(doc):
                                    text = page.get_text()
                                    if query in text:
                                        # సంబంధిత స్నిప్పెట్ సేకరించడం
                                        pos = text.find(query)
                                        snippet = text[max(0, pos-100):min(len(text), pos+300)].replace('\n', ' ')
                                        results.append({
                                            "book_title": f,
                                            "page_number": page_num + 1,
                                            "snippet": snippet
                                        })
                            except Exception as e:
                                continue
                
                if results:
                    st.success(f"మొత్తం {len(results)} ఫలితాలు కనుగొనబడ్డాయి!")
                    
                    # జెమిని AI ద్వారా RAG సారాంశ విశ్లేషణ
                    combined_text = "\n".join([f"గ్రంథం: {r['book_title']}, పేజీ: {r['page_number']}\nసందర్భం: {r['snippet']}" for r in results[:10]])
                    prompt = f"ఈ క్రింది తెలుగు ప్రాచీన గ్రంథాల సందర్భాల ఆధారంగా యూజర్ అడిగిన ప్రశ్న '{query}' కి చక్కటి విశ్లేషణాత్మక సారాంశాన్ని ఇవ్వండి:\n\n{combined_text}"
                    
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt
                    )
                    
                    st.markdown("### 🌟 గూగుల్ AI సారాంశ విశ్లేషణ:")
                    st.markdown(response.text)
                    
                    st.markdown("---")
                    st.markdown("### 📖 సంబంధిత గ్రంథ పేజీలు:")
                    for r in results:
                        with st.expander(f"📚 {r['book_title']} - పేజీ నంబర్: {r['page_number']}"):
                            st.write(r['snippet'])
                else:
                    st.warning("సంబంధిత ఫలితాలు ఏవీ కనుగొనబడలేదు.")
        else:
            st.warning("దయచేసి ఏదైనా పదాన్ని లేదా ప్రశ్నను టైప్ చేయండి.")

with tab2:
    st.subheader("ఎంచుకున్న పుస్తకం యొక్క పేజీల ప్రివ్యూ & OCR")
    if 'selected_pdf' in locals() and selected_pdf:
        st.write(f"ఎంచుకున్న ఫైల్: **{selected_pdf}**")
        pdf_path = os.path.join(LOCAL_DOWNLOAD_DIR, selected_pdf)
        doc = fitz.open(pdf_path)
        
        page_no = st.number_input("పేజీ నంబర్:", min_value=1, max_value=len(doc), value=1)
        if st.button("ఈ పేజీ టెక్స్ట్ చూపించు"):
            page = doc[page_no - 1]
            extracted_text = page.get_text()
            st.text_area("టెక్స్ట్ కంటెంట్:", extracted_text, height=300)