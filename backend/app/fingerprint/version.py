"""Fingerprint algorithm versions — bump these when algorithm changes to trigger recompute."""

EXTRACTION_VERSION = "1.0.0"      # trafilatura config
LEMMATIZATION_VERSION = "1.0.0"   # pymorphy3 + stopwords list
MINHASH_VERSION = "1.0.0"         # num_perm, shingle_size, seed
NGRAM_VERSION = "1.0.0"           # n_features, ngram_range, analyzer
NGRAM_FORMAT_VERSION = "v1"       # serialization format of bytea blob

FINGERPRINT_SCHEMA_VERSION = "1.0.0"  # composite, bump on any breaking change

# Configuration constants
STALENESS_DAYS = 30
THIN_CONTENT_CHARS = 200
MINHASH_NUM_PERM = 128
MINHASH_SEED = 42
SHINGLE_SIZE = 5
NGRAM_N_FEATURES = 2**18
NGRAM_RANGE = (3, 5)
MAX_TOKENS = 5000
BOILERPLATE_REVIEW_THRESHOLD = 0.7  # flag for QA review if above
