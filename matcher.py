# matcher.py

import os
if "/DEV" in os.path.dirname(os.path.abspath(__file__)) or os.path.dirname(os.path.abspath(__file__)).endswith("/DEV"):
    environment = "DEV"
else:
    environment = "PROD"
os.environ["JOB_SEARCH_ENV"] = environment
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def score_jobs(cfg, jobs, resume_text, user=None, test_mode=False):
    """
    Assigns similarity scores to each job based on the resume text.
    Supports:
      • 'tfidf'   – exact keyword overlap
      • 'semantic'– meaning-based comparison
      • 'hybrid'  – weighted combination of both
    Also applies optional boost_terms weighting from config.json.
    """
    
    # Import write_status if user provided
    write_status = None
    if user:
        try:
            from main import write_status as ws
            write_status = ws
        except:
            pass

    method = cfg.get("scoring_method", "tfidf").lower()
    msg = f"Using scoring method: {method}"
    print(f"DEBUG: {msg}")
    if write_status:
        write_status(user, test_mode, "running", msg)

    if not jobs:
        print("DEBUG: No jobs to score")
        return []

    scores = []

    # === SEMANTIC SCORING ===
    if method in ("semantic", "hybrid"):
        try:
            from sentence_transformers import SentenceTransformer, util
            model = SentenceTransformer("all-MiniLM-L6-v2")
            msg = "Loaded semantic model (all-MiniLM-L6-v2)"
            print(f"DEBUG: {msg}")
            if write_status:
                write_status(user, test_mode, "running", msg)

            # Prepare job texts
            job_texts = [
                j.get("title", "") + " " + j.get("description", "")
                for j in jobs
            ]
            
            # Encode resume once
            msg = "Encoding resume against semantic model..."
            print(f"DEBUG: {msg}")
            if write_status:
                write_status(user, test_mode, "running", msg)
            resume_embedding = model.encode(resume_text, convert_to_tensor=True, show_progress_bar=False)
            
            # Process jobs in batches to avoid memory issues
            batch_size = 32
            semantic_sims = []
            
            msg = f"Encoding {len(job_texts)} jobs in batches of {batch_size}..."
            print(f"DEBUG: {msg}")
            if write_status:
                write_status(user, test_mode, "running", msg)
                
            for i in range(0, len(job_texts), batch_size):
                batch = job_texts[i:i+batch_size]
                batch_embeddings = model.encode(batch, convert_to_tensor=True, show_progress_bar=False)
                batch_sims = util.cos_sim(resume_embedding, batch_embeddings)[0].cpu().numpy()
                semantic_sims.extend(batch_sims)
                
                # Progress indicator every 10 batches
                if (i // batch_size) % 10 == 0:
                    msg = f"Processed {i}/{len(job_texts)} jobs..."
                    print(f"DEBUG: {msg}")
                    if write_status:
                        write_status(user, test_mode, "running", msg)
            
            semantic_sims = np.array(semantic_sims)
            msg = f"Completed semantic scoring for {len(jobs)} jobs"
            print(f"DEBUG: {msg}")
            if write_status:
                write_status(user, test_mode, "running", msg)
            
        except Exception as e:
            msg = f"Semantic scoring failed ({e})"
            print(f"DEBUG: {msg}")
            if write_status:
                write_status(user, test_mode, "running", msg)
            semantic_sims = np.zeros(len(jobs))
            if method == "semantic":
                method = "tfidf"

    # === TF-IDF SCORING ===
    if method in ("tfidf", "hybrid"):
        try:
            msg = f"Starting TF-IDF scoring for {len(jobs)} jobs..."
            print(f"DEBUG: {msg}")
            if write_status:
                write_status(user, test_mode, "running", msg)
                
            vectorizer = TfidfVectorizer(stop_words="english")
            job_texts = [
                j.get("title", "") + " " + j.get("description", "")
                for j in jobs
            ]
            tfidf_matrix = vectorizer.fit_transform([resume_text] + job_texts)
            tfidf_sims = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:]).flatten()
            
            msg = "Completed TF-IDF scoring"
            print(f"DEBUG: {msg}")
            if write_status:
                write_status(user, test_mode, "running", msg)
        except Exception as e:
            print(f"DEBUG: TF-IDF scoring failed: {e}")
            tfidf_sims = np.zeros(len(jobs))

    # === COMBINE METHODS ===
    if method == "semantic":
        sims = semantic_sims
    elif method == "tfidf":
        sims = tfidf_sims
    elif method == "hybrid":
        # Weighted average (semantic favored slightly)
        sims = (0.6 * semantic_sims) + (0.4 * tfidf_sims)
    else:
        sims = np.zeros(len(jobs))

    # === BOOST TERM SCORING ===
    msg = "Applying boost terms and finalizing scores..."
    print(f"DEBUG: {msg}")
    if write_status:
        write_status(user, test_mode, "running", msg)
        
    boost_terms = [b.lower() for b in cfg.get("boost_terms", [])]
    boost_weight = cfg.get("boost_weight", 0.05)  # default +0.05 per match

    for i, job in enumerate(jobs):
        desc = (job.get("description", "") + " " + job.get("title", "")).lower()
        boost_score = sum(1 for term in boost_terms if term in desc)
        final_score = float(sims[i]) + (boost_score * boost_weight)
        job["score"] = round(min(final_score, 1.0), 3)  # cap at 1.0

    msg = f"Assigned similarity scores for {len(jobs)} jobs"
    print(f"DEBUG: {msg}")
    if write_status:
        write_status(user, test_mode, "running", msg)
    return jobs
