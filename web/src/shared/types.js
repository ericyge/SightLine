// JSDoc typedefs matching docs/CONTRACT.md. Keep field names identical to the backend.

/** @typedef {"clock_face"|"left_right"|"beeps_only"} GuidanceStyle */
/** @typedef {"far"|"near"|"reach"} Distance */

/**
 * @typedef {Object} Sighting
 * @property {number} id           stable saved sighting ID
 * @property {string} item
 * @property {string} room
 * @property {string} spot
 * @property {number[]} box        [x1, y1, x2, y2], 0..1
 * @property {number|null} confidence  null when the provider supplies no calibrated score
 * @property {string} ts           ISO timestamp
 * @property {string} thumb        URL path, e.g. /api/thumbs/abc.jpg ("" if none)
 * @property {string|null} place_id taught destination; null when unassigned
 * @property {"none"|"manual"|"automatic"} place_source automatic assignments are provisional inferences
 * @property {string} place_reason evidence summary or why the place was left unresolved
 */

/**
 * @typedef {Object} Place
 * @property {string} id
 * @property {string} name
 * @property {string} room
 * @property {string} description
 * @property {string[]} landmarks
 */

/**
 * @typedef {Object} WhereResponse
 * @property {string} item
 * @property {boolean} found
 * @property {{room:string, spot:string, minutes_ago:number, thumb:string, confidence:number|null}|null} last
 * @property {Sighting[]} history   up to 5
 * @property {Place|null} destination   newest sighting's taught place, not a route
 * @property {string} say
 */

/** @typedef {"visual"|"navigate"|"lost"} GuideMode */

/**
 * @typedef {Object} GuideResponse
 * @property {boolean} found
 * @property {number[]|null} point   [x, y]
 * @property {number[]|null} box
 * @property {number|null} angle     degrees, + is right
 * @property {number|null} clock     1..12
 * @property {Distance|null} distance
 * @property {number} pan            -1..1
 * @property {string} say            "" when nothing new to say
 * @property {number} beep_ms        0 = silent
 * @property {GuideMode} mode        visual when item in frame; navigate for room-to-room; lost otherwise
 * @property {string|null} current_place   localized place_id, or null
 * @property {Place|null} destination      newest sighting's place when known
 * @property {string|null} next_instruction  spoken connection instruction when mode is navigate
 */

/**
 * @typedef {Object} Profile
 * @property {{name:string, description:string, home_spot:string}[]} watchlist
 * @property {string[]} rooms
 * @property {GuidanceStyle} guidance_style
 */

/**
 * POST /api/chat response.
 * @typedef {Object} ChatResponse
 * @property {"find"|"where"|"stop"|"set_room"|"chitchat"} intent
 * @property {string|null} item    set for find / where
 * @property {string|null} room    set for set_room
 * @property {string} say          always what to speak
 * @property {string} provider     "mock" | "purple" | "openai_compat" | "keyword_fallback"
 */

/**
 * WebSocket /ws/events payloads. Every event also has `mock: boolean`.
 * @typedef {Object} SightlineEvent
 * @property {"sighting"|"guide"|"say"|"chat"} type
 * @property {boolean} mock
 * @property {Sighting[]} [sightings]  type "sighting"
 * @property {string|null} [frame]     data URL of the latest frame (sighting / guide)
 * @property {GuideMode} [mode]        type "guide"
 * @property {string|null} [current_place] type "guide"
 * @property {Place|null} [destination]    type "guide"
 * @property {string|null} [next_instruction] type "guide"
 * @property {string} [text]           type "say"
 * @property {string} [source]         type "say": "where" | "guide"
 * @property {string} [user_text]      type "chat": what the user said
 * @property {string} [intent]         type "chat"
 * @property {string} [provider]       type "chat"
 */

export {};
