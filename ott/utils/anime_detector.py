"""Anime detection utility with multi-signal analysis"""

import logging
from typing import Optional

logger = logging.getLogger("ott-hooks")


class AnimeDetector:
    """Multi-signal anime detection engine
    
    Analyzes series metadata from TMDB and AniList to determine if a series is anime.
    Uses weighted scoring across multiple signals:
    
    1. External IDs (100 points) - AniList/MAL IDs present
    2. Explicit anime genre (70 points) - TMDB has "Anime" genre
    3. Animation + Asian language (70 points) - Japanese/Chinese/Korean animation
    4. Keywords (50 points) - anime, donghua, manhwa, etc.
    5. Country + Animation (60 points) - JP/CN/KR animation
    
    Classification thresholds:
    - DEFINITE_ANIME: 100+ points (external IDs or strong signals)
    - LIKELY_ANIME: 60-99 points (multiple medium signals)
    - MAYBE_ANIME: 40-59 points (weak signals, needs manual review)
    - NOT_ANIME: <40 points
    """
    
    # Anime-specific keywords
    ANIME_KEYWORDS = {
        'anime', 'donghua', 'manhua', 'manhwa', 'aeni',
        'manga', 'seinen', 'shounen', 'shoujo', 'josei',
        'mecha', 'isekai', 'ecchi', 'harem'
    }
    
    # Asian language codes
    ASIAN_LANGUAGES = {'ja', 'zh', 'ko'}
    
    # Asian country codes
    ASIAN_COUNTRIES = {'JP', 'CN', 'KR'}
    
    def __init__(
        self,
        tmdb_client,
        anilist_client,
        require_anilist_match: bool = False
    ):
        """Initialize anime detector
        
        Args:
            tmdb_client: TMDBClient instance for metadata lookup
            anilist_client: AniListClient instance for anime ID lookup
            require_anilist_match: If True, only classify as anime if AniList match found
        """
        self.tmdb = tmdb_client
        self.anilist = anilist_client
        self.require_anilist_match = require_anilist_match
    
    def detect(
        self,
        title: str,
        year: Optional[int],
        tmdb_id: Optional[int] = None
    ) -> tuple[str, str, dict]:
        """Detect if a series is anime using multi-signal analysis
        
        Args:
            title: Series title
            year: Release year (optional)
            tmdb_id: TMDB series ID (optional, for metadata lookup)
            
        Returns:
            Tuple of (classification, reason, signals):
            - classification: "DEFINITE_ANIME", "LIKELY_ANIME", "MAYBE_ANIME", or "NOT_ANIME"
            - reason: Human-readable explanation of the classification
            - signals: Dict with detection signals and scores for logging
        """
        logger.info(f"[ANIME-DETECT] Analyzing: {title} ({year})")
        
        signals = {
            'title': title,
            'year': year,
            'tmdb_id': tmdb_id,
            'scores': {},
            'details': {}
        }
        
        # ─────────────────────────────────────────────
        # Fetch TMDB metadata
        # ─────────────────────────────────────────────
        tmdb_data = None
        if tmdb_id:
            tmdb_data = self.tmdb.get_series_details(tmdb_id)
            if not tmdb_data:
                logger.warning(f"[ANIME-DETECT] TMDB lookup failed for tmdb_id={tmdb_id}")
                signals['details']['tmdb_error'] = True
        
        # ─────────────────────────────────────────────
        # Fetch AniList data
        # ─────────────────────────────────────────────
        anilist_results = self.anilist.search_anime(title, year)
        anilist_match = None
        if anilist_results:
            anilist_match = self.anilist.get_best_match(anilist_results, title, year)
        
        signals['details']['anilist_results'] = len(anilist_results)
        signals['details']['anilist_match'] = anilist_match is not None
        
        # ─────────────────────────────────────────────
        # Signal 1: External Anime IDs (100 points)
        # ─────────────────────────────────────────────
        external_id_score = 0
        if anilist_match and self.anilist.has_anime_id(anilist_match):
            external_id_score = 100
            ids = self.anilist.extract_ids(anilist_match)
            signals['scores']['external_ids'] = external_id_score
            signals['details']['anime_ids'] = ids
            logger.info(f"[ANIME-DETECT] ✓ External IDs found: {ids}")
        
        # If strict mode and no AniList match, classify as NOT_ANIME
        if self.require_anilist_match and not anilist_match:
            reason = "No AniList match found (strict mode enabled)"
            logger.info(f"[ANIME-DETECT] → NOT_ANIME: {reason}")
            return ("NOT_ANIME", reason, signals)
        
        # ─────────────────────────────────────────────
        # Extract TMDB metadata for further signals
        # ─────────────────────────────────────────────
        genres = []
        keywords = []
        language = None
        countries = []
        
        if tmdb_data:
            genres = [g.lower() for g in self.tmdb.extract_genres(tmdb_data)]
            keywords = [k.lower() for k in self.tmdb.extract_keywords(tmdb_data)]
            language = self.tmdb.get_original_language(tmdb_data)
            countries = self.tmdb.get_origin_countries(tmdb_data)
            
            signals['details']['genres'] = genres
            signals['details']['keywords'] = keywords
            signals['details']['language'] = language
            signals['details']['countries'] = countries
        
        # ─────────────────────────────────────────────
        # Anti-Signal: Documentary exclusion
        # ─────────────────────────────────────────────
        if 'documentary' in genres and 'animation' not in genres:
            reason = "Documentary (non-animated)"
            logger.info(f"[ANIME-DETECT] → NOT_ANIME: {reason}")
            return ("NOT_ANIME", reason, signals)
        
        # ─────────────────────────────────────────────
        # Signal 2: Explicit Anime Genre (70 points)
        # ─────────────────────────────────────────────
        anime_genre_score = 0
        if 'anime' in genres:
            # Verify with language/country if available
            if language in self.ASIAN_LANGUAGES or any(c in self.ASIAN_COUNTRIES for c in countries):
                anime_genre_score = 70
                signals['scores']['anime_genre_verified'] = anime_genre_score
                logger.info(f"[ANIME-DETECT] ✓ Anime genre + Asian origin")
            else:
                anime_genre_score = 50
                signals['scores']['anime_genre_unverified'] = anime_genre_score
                logger.info(f"[ANIME-DETECT] ⚠ Anime genre (origin unverified)")
        
        # ─────────────────────────────────────────────
        # Signal 3: Animation + Asian Language (70 points)
        # ─────────────────────────────────────────────
        animation_language_score = 0
        if 'animation' in genres:
            if language == 'ja':
                # Japanese animation is almost always anime
                animation_language_score = 70
                signals['scores']['japanese_animation'] = animation_language_score
                logger.info(f"[ANIME-DETECT] ✓ Japanese animation")
            elif language in ['zh', 'ko'] and any(c in self.ASIAN_COUNTRIES for c in countries):
                # Chinese/Korean animation from CN/KR (donghua/aeni)
                animation_language_score = 70
                signals['scores']['asian_animation'] = animation_language_score
                logger.info(f"[ANIME-DETECT] ✓ {language.upper()} animation from {countries}")
        
        # ─────────────────────────────────────────────
        # Signal 4: Anime Keywords (50 points)
        # ─────────────────────────────────────────────
        keyword_score = 0
        matched_keywords = set(keywords) & self.ANIME_KEYWORDS
        if matched_keywords:
            keyword_score = 50
            signals['scores']['keywords'] = keyword_score
            signals['details']['matched_keywords'] = list(matched_keywords)
            logger.info(f"[ANIME-DETECT] ✓ Anime keywords: {matched_keywords}")
        
        # ─────────────────────────────────────────────
        # Signal 5: Country + Animation (60 points)
        # ─────────────────────────────────────────────
        country_animation_score = 0
        if 'animation' in genres and any(c in self.ASIAN_COUNTRIES for c in countries):
            # Fallback when language data missing
            if not language:
                country_animation_score = 60
                signals['scores']['country_animation'] = country_animation_score
                logger.info(f"[ANIME-DETECT] ✓ Animation from {countries} (no language data)")
        
        # ─────────────────────────────────────────────
        # Calculate total score
        # ─────────────────────────────────────────────
        total_score = (
            external_id_score +
            anime_genre_score +
            animation_language_score +
            keyword_score +
            country_animation_score
        )
        
        signals['total_score'] = total_score
        
        # ─────────────────────────────────────────────
        # Classification based on score
        # ─────────────────────────────────────────────
        if total_score >= 100:
            classification = "DEFINITE_ANIME"
            reason = self._build_reason(signals, classification)
        elif total_score >= 60:
            classification = "LIKELY_ANIME"
            reason = self._build_reason(signals, classification)
        elif total_score >= 40:
            classification = "MAYBE_ANIME"
            reason = self._build_reason(signals, classification)
        else:
            classification = "NOT_ANIME"
            reason = "Insufficient anime signals detected"
        
        logger.info(
            f"[ANIME-DETECT] → {classification} (score: {total_score}) | {reason}"
        )
        
        return (classification, reason, signals)
    
    def _build_reason(self, signals: dict, classification: str) -> str:
        """Build human-readable reason for classification
        
        Args:
            signals: Detection signals dict
            classification: Classification result
            
        Returns:
            Reason string
        """
        scores = signals.get('scores', {})
        details = signals.get('details', {})
        
        reasons = []
        
        # External IDs
        if 'external_ids' in scores:
            ids = details.get('anime_ids', {})
            id_list = [f"{k.upper()}:{v}" for k, v in ids.items()]
            reasons.append(f"Anime IDs present ({', '.join(id_list)})")
        
        # Anime genre
        if 'anime_genre_verified' in scores:
            reasons.append("Anime genre + Asian origin")
        elif 'anime_genre_unverified' in scores:
            reasons.append("Anime genre (unverified origin)")
        
        # Animation + language
        if 'japanese_animation' in scores:
            reasons.append("Japanese animation")
        elif 'asian_animation' in scores:
            lang = details.get('language', '?').upper()
            reasons.append(f"{lang} animation")
        
        # Keywords
        if 'keywords' in scores:
            kw = details.get('matched_keywords', [])
            reasons.append(f"Keywords: {', '.join(kw[:3])}")
        
        # Country + animation
        if 'country_animation' in scores:
            countries = details.get('countries', [])
            reasons.append(f"Animation from {', '.join(countries)}")
        
        if reasons:
            return " | ".join(reasons)
        else:
            return "No strong anime signals"
    
    def format_signals_for_notification(self, signals: dict) -> str:
        """Format detection signals for Telegram notification
        
        Args:
            signals: Detection signals dict from detect()
            
        Returns:
            Formatted string for Telegram message
        """
        details = signals.get('details', {})
        scores = signals.get('scores', {})
        
        lines = []
        
        # Animation genre
        genres = details.get('genres', [])
        has_animation = 'animation' in genres
        lines.append(f"  • Animation genre: {'✅' if has_animation else '❌'}")
        
        # Original language
        language = details.get('language')
        expected_langs = ', '.join(self.ASIAN_LANGUAGES)
        if language:
            is_asian = language in self.ASIAN_LANGUAGES
            lines.append(
                f"  • Original language: {language} "
                f"{'✅' if is_asian else f'(expected: {expected_langs})'}"
            )
        else:
            lines.append(f"  • Original language: Unknown")
        
        # Anime keywords
        matched_keywords = details.get('matched_keywords', [])
        if matched_keywords:
            lines.append(f"  • Anime keywords: ✅ ({', '.join(matched_keywords)})")
        else:
            lines.append(f"  • Anime keywords: ❌")
        
        # AniList match
        anilist_match = details.get('anilist_match', False)
        lines.append(f"  • AniList match: {'✅' if anilist_match else '❌'}")
        
        # Anime IDs
        if 'anime_ids' in details:
            ids = details['anime_ids']
            id_str = ', '.join([f"{k.upper()}:{v}" for k, v in ids.items()])
            lines.append(f"  • Anime IDs: {id_str}")
        
        # Total score
        total_score = signals.get('total_score', 0)
        lines.append(f"  • Confidence score: {total_score}/100")
        
        return '\n'.join(lines)
