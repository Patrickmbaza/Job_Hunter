"""Deterministic, explainable job-fit estimate; not an employer ATS score."""

from __future__ import annotations

import re

from app.models import Job, ProfessionalProfile
from app.services.job_sources import SKILLS, extract_skills

WEIGHTS = {
    "technical_skills": 30,
    "relevant_experience": 25,
    "responsibilities": 20,
    "seniority": 10,
    "location": 10,
    "qualifications": 5,
}
ROLE_FAMILIES = {
    "devops": ("devops", "cloud automation", "infrastructure automation"),
    "cloud": ("cloud engineer", "aws", "cloud infrastructure"),
    "sre": ("site reliability", "sre", "reliability engineer"),
    "platform": ("platform engineer", "platform engineering"),
    "devsecops": ("devsecops", "cloud security"),
    "mlops": ("mlops", "ai-devops", "machine learning operations"),
}
STOP = {"with", "from", "that", "this", "will", "have", "your", "their", "into", "using", "work", "team", "role", "you", "the", "and", "for", "are", "our", "about", "years", "year", "experience", "ability", "strong", "including", "such", "as"}


def _contains(text: str, term: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", text, re.I) is not None


def _years(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*years?", text, re.I)
    return float(match.group(1)) if match else None


def _factor(score: float, weight: float, reason: str, detail: dict) -> dict:
    return {"score": round(max(0.0, min(1.0, score)) * weight, 2), "weight": weight, "reason": reason, **detail}


def score_job(job: Job, profile: ProfessionalProfile) -> tuple[int, dict]:
    description = job.description or ""
    required = list(job.required_skills or [])
    preferred = list(job.preferred_skills or [])
    if not required and not preferred:
        required, preferred, extracted_experience = extract_skills(description)
    else:
        extracted_experience = None
    person_terms = " ".join((profile.skills or []) + (profile.certifications or []) + (profile.achievements or []))
    person_terms += " " + " ".join(profile.projects or [])
    employment_text = " ".join(str(item.get("raw_text", "")) for item in profile.employment_history or [] if isinstance(item, dict))
    evidence_text = f"{person_terms} {employment_text} {profile.experience_summary or ''}".casefold()
    matched_required = [skill for skill in required if _contains(evidence_text, skill)]
    missing_required = [skill for skill in required if skill not in matched_required]
    matched_preferred = [skill for skill in preferred if _contains(evidence_text, skill)]
    missing_preferred = [skill for skill in preferred if skill not in matched_preferred]
    required_coverage = len(matched_required) / len(required) if required else 0.65
    preferred_coverage = len(matched_preferred) / len(preferred) if preferred else 0.65
    skill_score = 0.75 * required_coverage + 0.25 * preferred_coverage

    needed_years = _years(job.experience_requirements or description)
    candidate_years = _years(profile.experience_summary or "")
    if needed_years and candidate_years:
        experience_score = min(candidate_years / needed_years, 1.0)
        experience_detail = f"Profile states {candidate_years:g}+ years; role asks for about {needed_years:g}+ years."
    elif needed_years:
        experience_score = 0.0
        experience_detail = "The posting gives a years requirement, but the verified profile has no comparable duration."
    else:
        experience_score = 0.5 if employment_text else 0.25
        experience_detail = "No explicit years threshold was found; experience evidence is incomplete."

    responsibilities = " ".join(
        line for line in description.splitlines()
        if re.search(r"responsibilit|what you.?ll do|your impact|you will|in this role", line, re.I)
    ) or description[:6000]
    terms = {term.casefold() for term in re.findall(r"[A-Za-z][A-Za-z0-9+#./-]{2,}", responsibilities) if term.casefold() not in STOP}
    experience_terms = {term.casefold() for term in re.findall(r"[A-Za-z][A-Za-z0-9+#./-]{2,}", evidence_text) if term.casefold() not in STOP}
    relevant_terms = terms & experience_terms
    responsibility_coverage = min(len(relevant_terms) / max(5, min(len(terms), 18)), 1.0)

    title = (job.title or "").casefold()
    target_titles = [item.casefold() for item in profile.target_titles or []]
    title_match = any(target in title or title in target for target in target_titles if target)
    level_terms = ("intern", "junior", "mid", "senior", "lead", "principal", "staff", "manager")
    job_level = next((word for word in level_terms if re.search(rf"\b{word}\b", title)), None)
    if job_level in {"senior", "lead", "principal", "staff"}:
        seniority_score = min((candidate_years or 0) / 5, 1.0) if candidate_years else 0.25
    elif job_level == "intern":
        seniority_score = 0.4
    else:
        seniority_score = 1.0 if title_match else 0.65

    location_text = f"{job.location or ''} {description[:12000]}".casefold()
    location_profile = (profile.location or "Nigeria").casefold()
    remote = job.work_mode == "remote" or bool(re.search(r"\b(remote|work from anywhere|distributed)\b", location_text))
    nigeria_eligible = "nigeria" in location_text or "africa" in location_text or "worldwide" in location_text or "global" in location_text
    if remote and (nigeria_eligible or not re.search(r"\b(us only|united states only|must reside in the us|uk only|europe only)\b", location_text)):
        location_score = 1.0
        location_reason = "Remote role appears compatible with the configured Nigeria location; confirm country restrictions with the employer."
    elif "nigeria" in location_text or any(city in location_text for city in ("port harcourt", "lagos", "abuja")):
        location_score = 1.0
        location_reason = "Job location is in Nigeria and aligns with the profile location."
    elif not job.location and not remote:
        location_score = 0.5
        location_reason = "Location compatibility cannot be determined from the posting."
    else:
        location_score = 0.0
        location_reason = "Posting location may not match the Nigeria-based profile; relocation or work authorization is unconfirmed."

    education_text = " ".join(profile.education or []).casefold()
    certification_text = " ".join(profile.certifications or []).casefold()
    qualification_text = (description + " " + (job.experience_requirements or "")).casefold()
    degree_requested = bool(re.search(r"\b(bachelor|b\.sc|b\.eng|b\.tech|degree)\b", qualification_text))
    cert_requested = bool(re.search(r"\b(certification|certified|aws cert|cka|ckad|security\+)\b", qualification_text))
    education_fit = 1.0 if degree_requested and education_text else (0.5 if degree_requested else 1.0)
    cert_fit = 1.0 if cert_requested and certification_text else (0.0 if cert_requested else 1.0)
    qualifications_score = (education_fit + cert_fit) / 2
    expected_education = "Bachelor of Technology in Electrical Engineering, Rivers State University"
    if not education_text and profile.verification_status != "verified":
        expected_education = "Education information from the user brief needs verification against the original CV."

    factors = {
        "technical_skills": _factor(skill_score, WEIGHTS["technical_skills"], "Required skills drive most of the skill score; preferred skills contribute less.", {
            "required_matched": matched_required, "required_missing": missing_required,
            "preferred_matched": matched_preferred, "preferred_missing": missing_preferred,
        }),
        "relevant_experience": _factor(experience_score, WEIGHTS["relevant_experience"], experience_detail, {
            "candidate_years_from_profile_text": candidate_years, "required_years_from_posting": needed_years,
        }),
        "responsibilities": _factor(responsibility_coverage, WEIGHTS["responsibilities"], "Keyword overlap between role responsibilities and profile evidence; this is a lightweight estimate.", {
            "overlap_terms": sorted(relevant_terms)[:30], "overlap_count": len(relevant_terms),
        }),
        "seniority": _factor(seniority_score, WEIGHTS["seniority"], "Title-family and level estimate; validate the actual scope with the employer.", {
            "target_title_match": title_match, "posting_level": job_level,
        }),
        "location": _factor(location_score, WEIGHTS["location"], location_reason, {"profile_location": profile.location, "remote": remote}),
        "qualifications": _factor(qualifications_score, WEIGHTS["qualifications"], "Education and certifications are checked only when the posting states a requirement.", {
            "education_on_profile": bool(education_text), "certifications_on_profile": bool(certification_text),
            "education_reference": expected_education,
        }),
    }
    score = round(sum(item["score"] for item in factors.values()))
    category = "High priority" if score >= 85 else "Good match" if score >= 70 else "Review" if score >= 55 else "Low priority"
    concerns = []
    if profile.verification_status != "verified":
        concerns.append("Profile claims are not verified against the original CV; score is provisional.")
    if missing_required:
        concerns.append("One or more required skill keywords were not found in the profile.")
    if needed_years and candidate_years and candidate_years < needed_years:
        concerns.append("The profile's stated years of experience are below the posting threshold.")
    if location_score == 0:
        concerns.append("Location, work authorization, or relocation compatibility needs review.")
    cv_emphasis = matched_required + [term for term in relevant_terms if term not in matched_required]
    report = {
        "score": score,
        "category": category,
        "estimate_disclaimer": "Internal profile-to-posting estimate only; this is not an employer ATS score.",
        "profile_verification": profile.verification_status,
        "weights": WEIGHTS,
        "factors": factors,
        "matching_skills": matched_required + matched_preferred,
        "missing_required_skills": missing_required,
        "missing_preferred_skills": missing_preferred,
        "relevant_experience": experience_detail,
        "potential_concerns": concerns,
        "recommended_next_action": "Review the original posting and verify profile evidence" if profile.verification_status != "verified" else "Review the role and prepare an application if interested",
        "suggested_cv_emphasis": cv_emphasis[:25],
    }
    return score, report
