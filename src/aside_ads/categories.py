"""Default category lists: sensitive contexts (never serve) and topics (for campaign include/exclude).

Terms are matched as whole phrases on normalized tokens ("queries" matches
"query"). Patterns are regular expressions on the lowercased text. Exemplars
are example utterances used only when an embedding model is available.

These lists are a starting point, not a guarantee. Extend them with
``ASIDE_SENSITIVE_TERMS_FILE`` (see README) for your app's audience and languages.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    name: str
    terms: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    exemplars: tuple[str, ...] = ()
    description: str = ""


# fmt: off
SENSITIVE: dict[str, Category] = {
    c.name: c
    for c in [
        Category(
            "self_harm",
            description="Suicide, self-harm, wanting to die",
            terms=(
                "suicide", "suicidal", "kill myself", "killing myself", "end my life", "ending my life",
                "take my own life", "want to die", "wanna die", "self harm", "selfharm", "hurt myself",
                "hurting myself", "cut myself", "cutting myself", "no reason to live", "better off dead",
                "dont want to live", "do not want to live", "end it all", "ending it all", "not wake up",
                "better off without me", "aatmahatya", "khudkushi", "आत्महत्या", "खुदकुशी", "ख़ुदकुशी",
            ),
            patterns=(
                r"मरना\s+चाहत", r"जीना\s+नहीं\s+चाहत",
            ),
            exemplars=(
                "I don't want to live anymore", "I'm thinking about ending my life",
                "I want to hurt myself", "nobody would miss me if I was gone",
            ),
        ),
        Category(
            "medical_emergency",
            description="Urgent medical situations",
            terms=(
                "chest pain", "heart attack", "having a stroke", "stroke symptom", "cant breathe",
                "cannot breathe", "trouble breathing", "difficulty breathing", "not breathing",
                "unconscious", "passed out", "seizure", "overdose", "overdosed", "anaphylaxis",
                "allergic reaction", "severe bleeding", "bleeding heavily", "poisoned", "poisoning",
                "swallowed bleach", "call an ambulance", "emergency room", "call 911", "call 112",
                "call 108", "wont wake up", "took too many pills",
            ),
            exemplars=(
                "my father has chest pain and his left arm is numb",
                "someone collapsed and is not breathing",
                "my throat is swelling up after eating peanuts",
            ),
        ),
        Category(
            "serious_illness",
            description="Serious diagnoses and end-of-life care",
            terms=(
                "cancer", "chemotherapy", "chemo", "tumor", "tumour", "terminal illness",
                "terminally ill", "hiv", "miscarriage", "stillbirth", "palliative care", "hospice",
                "dialysis", "diagnosed with",
            ),
            exemplars=(
                "I was just diagnosed with cancer", "my mother is in hospice care",
            ),
        ),
        Category(
            "minors",
            description="The user appears to be a child or teenager",
            terms=(
                "middle school", "junior high", "high school student", "im in high school",
            ),
            patterns=(
                r"\b(?:i'?m|i am|im)\s+(?:only\s+|just\s+)?(?:[5-9]|1[0-7])\s*(?:years?|yrs?)\s*old\b",
                r"\b(?:i'?m|i am|im)\s+(?:only\s+|just\s+)?(?:[5-9]|1[0-7])\s*(?:yo|y/o)\b",
                r"\b(?:in|my)\s+(?:class|grade|std|standard)\s+(?:[1-9]|10)\b",
                r"\b(?:[1-9]|10)(?:st|nd|rd|th)\s+(?:grade|class|std|standard)\b",
            ),
            exemplars=(
                "I'm 13 and my parents are really strict", "I'm in 8th grade and need help",
            ),
        ),
        Category(
            "abuse_violence",
            description="Abuse, assault, domestic violence",
            terms=(
                "domestic violence", "abusive relationship", "being abused", "sexual assault",
                "sexually assaulted", "raped", "rape", "molested", "stalking me", "threatened to kill",
            ),
            patterns=(
                r"\b(?:husband|wife|partner|boyfriend|girlfriend|father|dad|mother|mom|he|she|they)\s+(?:hits|beats|slaps|chokes|kicks|hurts)\s+me\b",
            ),
            exemplars=(
                "my husband hits me when he is angry", "I was assaulted last night",
            ),
        ),
        Category(
            "grief",
            description="Death and bereavement",
            terms=(
                "passed away", "funeral", "grieving", "grief", "bereavement", "condolence",
                "lost my mother", "lost my father", "lost my mom", "lost my dad", "lost my wife",
                "lost my husband", "lost my son", "lost my daughter", "lost my baby", "death of my",
            ),
            exemplars=(
                "my mom passed away last week", "how do I cope with the death of my dog",
            ),
        ),
        Category(
            "addiction",
            description="Substance abuse and gambling problems",
            terms=(
                "addicted", "addiction", "relapse", "relapsed", "withdrawal symptom", "rehab",
                "alcoholic", "cant stop drinking", "gambling problem", "gambling addiction",
                "gambling debt", "gambling away", "drug problem", "sobriety",
            ),
            exemplars=(
                "I can't stop drinking every night", "I relapsed after six months clean",
            ),
        ),
        Category(
            "financial_distress",
            description="Debt crises, eviction, inability to afford basics",
            terms=(
                "bankrupt", "bankruptcy", "cant pay rent", "cant pay my rent", "cant afford food",
                "eviction", "evicted", "debt collector", "loan shark", "foreclosure", "drowning in debt",
                "cant afford groceries", "cant pay for groceries",
            ),
            exemplars=(
                "I can't afford rent this month and I'm about to be evicted",
                "debt collectors keep calling me every day",
            ),
        ),
    ]
}


TOPICS: dict[str, Category] = {
    c.name: c
    for c in [
        Category(
            "software_development",
            terms=(
                "code", "coding", "programming", "python", "javascript", "typescript", "java", "rustlang",
                "rust code", "rust programming", "borrow checker", "golang", "api", "bug", "debug",
                "compile", "git", "github", "database", "sql",
                "postgres", "deploy", "kubernetes", "docker", "ci", "framework", "react", "backend",
                "frontend", "function", "query",
            ),
            exemplars=(
                "how do I fix this bug in my python code", "my postgres query is slow",
            ),
        ),
        Category(
            "ai_ml",
            terms=(
                "llm", "gpt", "machine learning", "neural network", "fine tune", "finetune", "embedding",
                "transformer", "gpu", "training data", "inference", "model training",
            ),
            exemplars=(
                "how do I fine-tune a language model", "which GPU should I use for training",
            ),
        ),
        Category(
            "finance",
            terms=(
                "invest", "investment", "stock", "mutual fund", "sip", "tax", "gst", "income tax",
                "loan", "credit card", "bank", "savings", "budget", "accounting", "invoice", "insurance",
                "retirement", "crypto",
            ),
            exemplars=(
                "should I invest in index funds", "how do I file my GST return",
            ),
        ),
        Category(
            "travel",
            terms=(
                "trip", "travel", "flight", "hotel", "visa", "passport", "itinerary", "vacation",
                "holiday", "airport", "luggage", "baggage", "backpacking", "tourist",
            ),
            exemplars=(
                "planning a trip to Japan next month", "what should I pack for a beach vacation",
            ),
        ),
        Category(
            "food_cooking",
            terms=(
                "recipe", "cook", "cooking", "bake", "baking", "bread", "sourdough", "dinner", "lunch",
                "breakfast", "restaurant", "vegan", "vegetarian", "ingredient", "oven", "curry",
                "kitchen", "meal", "dish", "chef", "cuisine", "cookware", "cast iron", "frying pan",
                "non stick", "nonstick", "pressure cooker", "mixer grinder", "wet grinder", "fry", "frying",
                "grill", "roast", "boil", "marinade", "marinate", "dough", "flour", "cake", "dessert",
                "snack", "spice", "spices", "chicken", "egg", "rice", "dal", "paneer", "curd", "roti",
                "rotis", "chapati", "paratha", "biryani", "dosa", "idli", "masala", "chutney", "sabzi",
                "tadka", "samosa", "kadhai", "kadai", "tawa",
            ),
            exemplars=(
                "give me a recipe for banana bread", "what can I cook with rice and lentils",
                "how do I stop my cast iron pan from rusting", "which pan is best for making dosa",
            ),
        ),
        Category(
            "health_fitness",
            terms=(
                "workout", "exercise", "gym", "fitness", "diet", "protein", "calorie", "running", "yoga",
                "sleep", "weight loss", "stretching",
            ),
            exemplars=(
                "a beginner workout plan for the gym", "how much protein do I need",
            ),
        ),
        Category(
            "education",
            terms=(
                "learn", "learning", "course", "exam", "study", "studying", "university", "college",
                "homework", "language", "spanish", "hindi", "japanese", "french", "tutorial",
            ),
            exemplars=(
                "how do I prepare for the GRE", "what's the fastest way to learn Spanish",
            ),
        ),
        Category(
            "shopping",
            terms=(
                "buy", "price", "discount", "deal", "cheap", "review", "laptop", "phone", "headphones",
            ),
            exemplars=(
                "which laptop should I buy under 1000 dollars",
            ),
        ),
        Category(
            "entertainment",
            terms=(
                "movie", "film", "series", "netflix", "music", "song", "book", "novel", "anime", "poem",
            ),
            exemplars=(
                "recommend a good sci-fi movie", "write a poem about autumn",
            ),
        ),
        Category(
            "sports",
            terms=(
                "cricket", "football", "soccer", "ipl", "nba", "tennis", "match", "score", "team",
            ),
            exemplars=(
                "who won the cricket match yesterday",
            ),
        ),
        Category(
            "business",
            terms=(
                "startup", "marketing", "sales", "customer", "revenue", "pitch", "investor", "hiring",
                "small business", "freelance", "freelancer", "saas",
            ),
            exemplars=(
                "how do I find my first customers for a startup",
            ),
        ),
        Category(
            "gaming",
            terms=(
                "game", "gaming", "playstation", "xbox", "steam deck", "steam game", "steam library",
                "nintendo", "minecraft", "valorant",
            ),
            exemplars=(
                "best settings for valorant on a low-end pc",
            ),
        ),
        Category(
            "news_politics",
            terms=(
                "election", "politics", "political", "government", "minister", "parliament", "war",
                "protest",
            ),
            exemplars=(
                "what happened in the election results",
            ),
        ),
        Category(
            "parenting",
            terms=(
                "toddler", "baby", "newborn", "parenting", "my kid", "my child", "daycare", "diaper",
            ),
            exemplars=(
                "how do I get my toddler to sleep through the night",
            ),
        ),
        Category(
            "legal",
            terms=(
                "lawyer", "lawsuit", "contract", "court", "legal", "sue", "visa lawyer", "tenant rights",
            ),
            exemplars=(
                "can my landlord keep my security deposit",
            ),
        ),
    ]
}
# fmt: on
