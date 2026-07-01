"""Seed data for the gym tracker: exercise library, routine templates, and the
exercise list for each routine. Imported by ``database.init_gym_data`` to
populate the ``gym_*`` tables on boot (idempotent — existing rows are left
alone). This module is pure data with no DB or app imports.
"""

EXERCISES = [
    # PUSH DAY — Chest
    {
        "name": "Barbell Bench Press",
        "muscle_group": "chest",
        "secondary_muscles": ["triceps", "shoulders"],
        "equipment": "barbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=vcBig73ojpE",
        "instructions": "1. Lie flat on bench\n2. Grip bar slightly wider than shoulder width\n3. Lower bar to chest with control\n4. Press up explosively\n5. Keep feet flat on floor, back neutral",
        "tips": "Keep shoulder blades retracted. Don't bounce bar off chest. Control the descent.",
        "rank_bronze": 40, "rank_silver": 60, "rank_gold": 80, "rank_platinum": 100, "rank_diamond": 120
    },
    {
        "name": "Incline Dumbbell Press",
        "muscle_group": "chest",
        "secondary_muscles": ["triceps", "shoulders"],
        "equipment": "dumbbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=8iPEnn-ltC8",
        "instructions": "1. Set bench to 30-45 degrees\n2. Hold dumbbells at chest height\n3. Press up and slightly inward\n4. Lower with control\n5. Keep core tight",
        "tips": "Don't flare elbows too wide. Squeeze chest at top.",
        "rank_bronze": 10, "rank_silver": 16, "rank_gold": 22, "rank_platinum": 28, "rank_diamond": 35
    },
    {
        "name": "Cable Chest Fly",
        "muscle_group": "chest",
        "secondary_muscles": [],
        "equipment": "cable",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=Iwe6AmxVf7o",
        "instructions": "1. Set cables at shoulder height\n2. Stand in centre, step forward\n3. Bring handles together in arc motion\n4. Squeeze chest at centre\n5. Return slowly",
        "tips": "Slight bend in elbows throughout. Focus on chest contraction not arm movement.",
        "rank_bronze": 10, "rank_silver": 18, "rank_gold": 26, "rank_platinum": 35, "rank_diamond": 45
    },
    # PUSH DAY — Shoulders
    {
        "name": "Seated Dumbbell Shoulder Press",
        "muscle_group": "shoulders",
        "secondary_muscles": ["triceps"],
        "equipment": "dumbbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=qEwKCR5JCog",
        "instructions": "1. Sit on adjustable bench set to 90 degrees\n2. Hold dumbbells at shoulder height\n3. Press straight up\n4. Lower with control\n5. Don't lock out elbows",
        "tips": "Keep core braced. Don't arch lower back excessively.",
        "rank_bronze": 10, "rank_silver": 16, "rank_gold": 22, "rank_platinum": 30, "rank_diamond": 38
    },
    {
        "name": "Lateral Raises",
        "muscle_group": "shoulders",
        "secondary_muscles": [],
        "equipment": "dumbbell",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=3VcKaXpzqRo",
        "instructions": "1. Stand with dumbbells at sides\n2. Raise arms out to sides to shoulder height\n3. Slight bend in elbows\n4. Lower slowly — 3 seconds down\n5. Don't swing",
        "tips": "Go lighter than you think. Slow controlled reps beat heavy swinging every time.",
        "rank_bronze": 5, "rank_silver": 8, "rank_gold": 12, "rank_platinum": 16, "rank_diamond": 20
    },
    {
        "name": "Cable Lateral Raises",
        "muscle_group": "shoulders",
        "secondary_muscles": [],
        "equipment": "cable",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=0KnFCTSOx8k",
        "instructions": "1. Set cable to lowest position\n2. Stand side-on, grip handle with far hand\n3. Raise arm out to the side to shoulder height\n4. Slight bend in elbow\n5. Lower slowly — cable keeps constant tension",
        "tips": "Cable keeps tension at the bottom where dumbbells lose it. Lighter is better — control every rep.",
        "rank_bronze": 5, "rank_silver": 8, "rank_gold": 12, "rank_platinum": 16, "rank_diamond": 20
    },
    {
        "name": "Face Pulls",
        "muscle_group": "shoulders",
        "secondary_muscles": ["back"],
        "equipment": "cable",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=rep-qVOkqgk",
        "instructions": "1. Set cable at face height with rope attachment\n2. Pull rope towards face\n3. Elbows flare out and back\n4. Squeeze rear delts\n5. Return slowly",
        "tips": "Great for shoulder health and posture. Do these every session.",
        "rank_bronze": 10, "rank_silver": 18, "rank_gold": 26, "rank_platinum": 35, "rank_diamond": 45
    },
    # PUSH DAY — Triceps
    {
        "name": "Tricep Rope Pushdown",
        "muscle_group": "triceps",
        "secondary_muscles": [],
        "equipment": "cable",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=vB5OHsJ3EME",
        "instructions": "1. Set cable high with rope attachment\n2. Hold rope with both hands\n3. Push down and spread rope at bottom\n4. Squeeze triceps\n5. Return slowly",
        "tips": "Keep elbows pinned to sides. Full extension at bottom.",
        "rank_bronze": 15, "rank_silver": 25, "rank_gold": 35, "rank_platinum": 45, "rank_diamond": 55
    },
    {
        "name": "Overhead Tricep Extension",
        "muscle_group": "triceps",
        "secondary_muscles": [],
        "equipment": "cable",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=YbX7Wd8jQ-Q",
        "instructions": "1. Set cable low with rope attachment\n2. Face away from cable\n3. Hold rope overhead\n4. Extend arms overhead\n5. Lower slowly behind head",
        "tips": "Long head of tricep gets fully stretched here. Great for size.",
        "rank_bronze": 10, "rank_silver": 18, "rank_gold": 26, "rank_platinum": 35, "rank_diamond": 45
    },
    {
        "name": "Dips",
        "muscle_group": "triceps",
        "secondary_muscles": ["chest", "shoulders"],
        "equipment": "bodyweight",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=2z8JmcrW-As",
        "instructions": "1. Grip parallel bars\n2. Lower body until upper arms parallel to floor\n3. Press back up\n4. Keep torso upright for tricep focus\n5. Lean forward for chest focus",
        "tips": "Use assisted dip machine if needed. Work towards bodyweight dips.",
        "rank_bronze": 0, "rank_silver": 10, "rank_gold": 20, "rank_platinum": 40, "rank_diamond": 60
    },
    # PULL DAY — Back
    {
        "name": "Barbell Row",
        "muscle_group": "back",
        "secondary_muscles": ["biceps"],
        "equipment": "barbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=FWJR5Ve8bnQ",
        "instructions": "1. Hinge at hips, back flat\n2. Grip bar shoulder width\n3. Pull bar to lower chest/upper abdomen\n4. Squeeze shoulder blades\n5. Lower with control",
        "tips": "Keep back flat — never round. Lead with elbows not hands.",
        "rank_bronze": 40, "rank_silver": 60, "rank_gold": 80, "rank_platinum": 100, "rank_diamond": 120
    },
    {
        "name": "Lat Pulldown",
        "muscle_group": "back",
        "secondary_muscles": ["biceps"],
        "equipment": "machine",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=CAwf7n6Tuhs",
        "instructions": "1. Grip bar wide\n2. Lean back slightly\n3. Pull bar to upper chest\n4. Squeeze lats at bottom\n5. Return slowly — full stretch at top",
        "tips": "Imagine pulling your elbows to your hips. Don't use momentum.",
        "rank_bronze": 30, "rank_silver": 45, "rank_gold": 60, "rank_platinum": 75, "rank_diamond": 90
    },
    {
        "name": "Seated Cable Row",
        "muscle_group": "back",
        "secondary_muscles": ["biceps"],
        "equipment": "cable",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=GZbfZ033f74",
        "instructions": "1. Sit at cable row station\n2. Grip handle with both hands\n3. Pull to abdomen\n4. Squeeze shoulder blades together\n5. Return slowly with full stretch",
        "tips": "Don't lean back excessively. Control the return.",
        "rank_bronze": 30, "rank_silver": 45, "rank_gold": 60, "rank_platinum": 80, "rank_diamond": 100
    },
    {
        "name": "Pull-ups",
        "muscle_group": "back",
        "secondary_muscles": ["biceps"],
        "equipment": "bodyweight",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=eGo4IYlbE5g",
        "instructions": "1. Grip bar slightly wider than shoulders\n2. Hang with arms fully extended\n3. Pull up until chin over bar\n4. Lower slowly\n5. Use full range of motion",
        "tips": "Use assisted pull-up machine if needed. Work towards unassisted.",
        "rank_bronze": 1, "rank_silver": 5, "rank_gold": 10, "rank_platinum": 15, "rank_diamond": 20
    },
    # PULL DAY — Biceps
    {
        "name": "Incline Dumbbell Curl",
        "muscle_group": "biceps",
        "secondary_muscles": [],
        "equipment": "dumbbell",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=soxrZlIl35U",
        "instructions": "1. Set bench to 45-60 degrees\n2. Sit back with arms hanging\n3. Curl both dumbbells up\n4. Squeeze at top\n5. Lower slowly — full stretch at bottom",
        "tips": "Great stretch on the long head of bicep. Don't rush the negative.",
        "rank_bronze": 6, "rank_silver": 10, "rank_gold": 14, "rank_platinum": 18, "rank_diamond": 22
    },
    {
        "name": "Hammer Curls",
        "muscle_group": "biceps",
        "secondary_muscles": ["forearms"],
        "equipment": "dumbbell",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=zC3nLlEvin4",
        "instructions": "1. Hold dumbbells with neutral grip (palms facing each other)\n2. Curl up keeping neutral grip\n3. Squeeze at top\n4. Lower slowly",
        "tips": "Builds brachialis which pushes bicep up making it look bigger.",
        "rank_bronze": 8, "rank_silver": 12, "rank_gold": 16, "rank_platinum": 20, "rank_diamond": 26
    },
    {
        "name": "Cable Bicep Curl",
        "muscle_group": "biceps",
        "secondary_muscles": [],
        "equipment": "cable",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=NFzTWp2qpiE",
        "instructions": "1. Set cable low with bar or rope\n2. Curl up keeping elbows pinned\n3. Squeeze at top\n4. Lower slowly — cable keeps tension throughout",
        "tips": "Cable keeps constant tension unlike dumbbells. Great finisher.",
        "rank_bronze": 10, "rank_silver": 16, "rank_gold": 22, "rank_platinum": 28, "rank_diamond": 35
    },
    # LEGS DAY
    {
        "name": "Barbell Squat",
        "muscle_group": "quads",
        "secondary_muscles": ["hamstrings", "glutes", "core"],
        "equipment": "barbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=ultWZbUMPL8",
        "instructions": "1. Bar on upper traps\n2. Feet shoulder width, toes slightly out\n3. Squat down until thighs parallel\n4. Drive up through heels\n5. Keep chest up throughout",
        "tips": "Most important exercise you can do. Master form before adding weight. Record yourself.",
        "rank_bronze": 40, "rank_silver": 65, "rank_gold": 90, "rank_platinum": 115, "rank_diamond": 140
    },
    {
        "name": "Leg Press",
        "muscle_group": "quads",
        "secondary_muscles": ["hamstrings", "glutes"],
        "equipment": "machine",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=IZxyjW7MPJQ",
        "instructions": "1. Sit in machine, feet shoulder width on platform\n2. Lower until 90 degree knee angle\n3. Press up without locking out\n4. Control the descent\n5. Don't let lower back peel off seat",
        "tips": "Higher feet = more hamstrings. Lower feet = more quads.",
        "rank_bronze": 60, "rank_silver": 100, "rank_gold": 140, "rank_platinum": 180, "rank_diamond": 220
    },
    {
        "name": "Romanian Deadlift",
        "muscle_group": "hamstrings",
        "secondary_muscles": ["glutes", "back"],
        "equipment": "barbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=JCXUYuzwNrM",
        "instructions": "1. Hold bar at hip width\n2. Hinge at hips pushing them back\n3. Lower bar along legs to mid-shin\n4. Feel hamstring stretch\n5. Drive hips forward to stand",
        "tips": "Keep bar close to legs. Soft bend in knees. Never round lower back.",
        "rank_bronze": 40, "rank_silver": 65, "rank_gold": 90, "rank_platinum": 115, "rank_diamond": 145
    },
    {
        "name": "Leg Extension",
        "muscle_group": "quads",
        "secondary_muscles": [],
        "equipment": "machine",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=YyvSfVjQeL0",
        "instructions": "1. Sit in machine, pad on shins\n2. Extend legs fully\n3. Squeeze quads at top\n4. Lower slowly — 3 seconds down\n5. Don't let weight slam",
        "tips": "Full extension and slow negative. Quality reps over heavy weight.",
        "rank_bronze": 20, "rank_silver": 35, "rank_gold": 50, "rank_platinum": 65, "rank_diamond": 80
    },
    {
        "name": "Leg Curl",
        "muscle_group": "hamstrings",
        "secondary_muscles": [],
        "equipment": "machine",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=1Tq3QdYUuHs",
        "instructions": "1. Lie face down on machine\n2. Pad behind ankles\n3. Curl heels towards glutes\n4. Squeeze hamstrings at top\n5. Lower slowly",
        "tips": "Point toes for more hamstring activation. Slow negative.",
        "rank_bronze": 20, "rank_silver": 35, "rank_gold": 50, "rank_platinum": 65, "rank_diamond": 80
    },
    {
        "name": "Standing Calf Raise",
        "muscle_group": "calves",
        "secondary_muscles": [],
        "equipment": "machine",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=-M4-G8p1fCI",
        "instructions": "1. Stand on calf raise machine\n2. Full stretch at bottom — heel below platform\n3. Rise up on toes as high as possible\n4. Hold at top 1 second\n5. Lower slowly",
        "tips": "Full range of motion is key. Calves respond well to high reps and stretch.",
        "rank_bronze": 30, "rank_silver": 50, "rank_gold": 70, "rank_platinum": 90, "rank_diamond": 110
    },
    {
        "name": "Ab Cruncher",
        "muscle_group": "core",
        "secondary_muscles": [],
        "equipment": "machine",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=Xyd_fa5zoEU",
        "instructions": "1. Sit in ab machine\n2. Cross arms over chest\n3. Crunch forward squeezing abs\n4. Hold 1 second\n5. Return slowly",
        "tips": "Focus on the contraction not the weight. Breathe out as you crunch.",
        "rank_bronze": 20, "rank_silver": 35, "rank_gold": 50, "rank_platinum": 65, "rank_diamond": 80
    },
    {
        "name": "Walking Lunges",
        "muscle_group": "quads",
        "secondary_muscles": ["hamstrings", "glutes"],
        "equipment": "dumbbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=L8fvypPrzzs",
        "instructions": "1. Hold dumbbells at sides\n2. Step forward into lunge\n3. Back knee almost touches floor\n4. Push off front foot\n5. Step through into next lunge",
        "tips": "Keep torso upright. Long stride for more glute activation.",
        "rank_bronze": 0, "rank_silver": 10, "rank_gold": 18, "rank_platinum": 26, "rank_diamond": 34
    },
    {
        "name": "Seated Calf Raise",
        "muscle_group": "calves",
        "secondary_muscles": [],
        "equipment": "machine",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=JbyjNymZOt0",
        "instructions": "1. Sit with pad on thighs\n2. Full stretch at bottom\n3. Rise up on toes\n4. Hold at top\n5. Lower slowly",
        "tips": "Targets soleus (inner calf). Do both standing and seated for full calf development.",
        "rank_bronze": 20, "rank_silver": 35, "rank_gold": 50, "rank_platinum": 65, "rank_diamond": 80
    },
    {
        "name": "Smith Machine Squat",
        "muscle_group": "quads",
        "secondary_muscles": ["hamstrings", "glutes"],
        "equipment": "machine",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=XuFhWuNSAHk",
        "instructions": "1. Set bar on upper traps\n2. Feet slightly forward of bar\n3. Squat down to parallel\n4. Drive up through heels\n5. Keep chest up",
        "tips": "Feet further forward than free squat due to fixed bar path.",
        "rank_bronze": 40, "rank_silver": 65, "rank_gold": 90, "rank_platinum": 115, "rank_diamond": 140
    },
    {
        "name": "Smith Machine Incline Press",
        "muscle_group": "chest",
        "secondary_muscles": ["triceps", "shoulders"],
        "equipment": "machine",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=EdtPPGqNrFo",
        "instructions": "1. Set bench to 30-45 degrees under Smith machine\n2. Grip bar slightly wider than shoulders\n3. Lower to upper chest\n4. Press up\n5. Keep shoulder blades retracted",
        "tips": "Great for upper chest isolation. Safer than free bar incline.",
        "rank_bronze": 30, "rank_silver": 50, "rank_gold": 70, "rank_platinum": 90, "rank_diamond": 110
    },
    {
        "name": "Chest Press Machine",
        "muscle_group": "chest",
        "secondary_muscles": ["triceps"],
        "equipment": "machine",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=xUm0BiZCX_I",
        "instructions": "1. Adjust seat so handles at chest height\n2. Press forward fully\n3. Squeeze chest at full extension\n4. Return slowly\n5. Don't let weight stack touch between reps",
        "tips": "Good for beginners learning chest activation. Focus on the squeeze.",
        "rank_bronze": 30, "rank_silver": 50, "rank_gold": 70, "rank_platinum": 90, "rank_diamond": 110
    },
    {
        "name": "Dumbbell Row",
        "muscle_group": "back",
        "secondary_muscles": ["biceps"],
        "equipment": "dumbbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=roCP6wCXPqo",
        "instructions": "1. Place one knee and hand on bench\n2. Hold dumbbell in other hand\n3. Row up to hip\n4. Squeeze lat at top\n5. Lower fully",
        "tips": "Drive elbow back not up. Full stretch at bottom.",
        "rank_bronze": 16, "rank_silver": 24, "rank_gold": 32, "rank_platinum": 40, "rank_diamond": 50
    },
    {
        "name": "Lat Pulldown Close Grip",
        "muscle_group": "back",
        "secondary_muscles": ["biceps"],
        "equipment": "machine",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=1xMaFs0L3ao",
        "instructions": "1. Use close grip or V-bar attachment\n2. Pull to upper chest\n3. Elbows come down and back\n4. Squeeze lats\n5. Full stretch at top",
        "tips": "Closer grip hits lower lats more. Good variation from wide grip.",
        "rank_bronze": 30, "rank_silver": 45, "rank_gold": 60, "rank_platinum": 75, "rank_diamond": 90
    },
    {
        "name": "Incline Walk",
        "muscle_group": "cardio",
        "secondary_muscles": [],
        "equipment": "treadmill",
        "exercise_type": "cardio",
        "youtube_url": "https://www.youtube.com/watch?v=HbVCsJAOr5Y",
        "instructions": "1. Set treadmill to speed 3.5\n2. Set incline to 13\n3. Walk for 25 minutes\n4. Hold sides only if necessary\n5. Keep upright posture",
        "tips": "Don't hold the rails — it reduces calorie burn significantly. Swing arms naturally.",
        "rank_bronze": 10, "rank_silver": 20, "rank_gold": 30, "rank_platinum": 45, "rank_diamond": 60
    },
]

ROUTINES = [
    {"name": "Push Day", "day_type": "push", "description": "Chest, shoulders and triceps", "order_index": 0},
    {"name": "Pull Day", "day_type": "pull", "description": "Back and biceps", "order_index": 1},
    {"name": "Legs Day", "day_type": "legs", "description": "Quads, hamstrings and calves", "order_index": 2},
    {"name": "Upper Day", "day_type": "upper", "description": "Full upper body", "order_index": 3},
    {"name": "Lower Day", "day_type": "lower", "description": "Full lower body", "order_index": 4},
]

# (exercise_name, sets, rep_min, rep_max, rest_seconds)
ROUTINE_EXERCISES = {
    "Push Day": [
        ("Barbell Bench Press", 4, 8, 10, 90),
        ("Incline Dumbbell Press", 3, 10, 12, 75),
        ("Cable Chest Fly", 3, 12, 15, 60),
        ("Seated Dumbbell Shoulder Press", 3, 10, 12, 75),
        ("Lateral Raises", 4, 15, 20, 45),
        ("Tricep Rope Pushdown", 3, 12, 15, 60),
        ("Overhead Tricep Extension", 3, 12, 15, 60),
        ("Incline Walk", 1, 25, 25, 0),
    ],
    "Pull Day": [
        ("Barbell Row", 4, 8, 10, 90),
        ("Lat Pulldown", 3, 10, 12, 75),
        ("Seated Cable Row", 3, 10, 12, 75),
        ("Face Pulls", 3, 15, 20, 45),
        ("Incline Dumbbell Curl", 3, 10, 12, 60),
        ("Hammer Curls", 3, 12, 15, 60),
        ("Pull-ups", 3, 1, 20, 90),
        ("Incline Walk", 1, 25, 25, 0),
    ],
    "Legs Day": [
        ("Barbell Squat", 4, 8, 10, 90),
        ("Leg Press", 3, 10, 12, 75),
        ("Romanian Deadlift", 3, 10, 12, 75),
        ("Leg Extension", 3, 12, 15, 60),
        ("Leg Curl", 3, 12, 15, 60),
        ("Standing Calf Raise", 4, 15, 20, 45),
        ("Ab Cruncher", 3, 15, 20, 45),
        ("Incline Walk", 1, 25, 25, 0),
    ],
    "Upper Day": [
        ("Smith Machine Incline Press", 4, 8, 10, 90),
        ("Chest Press Machine", 3, 10, 12, 75),
        ("Cable Chest Fly", 3, 12, 15, 60),
        ("Seated Dumbbell Shoulder Press", 3, 10, 12, 75),
        ("Lateral Raises", 3, 15, 20, 45),
        ("Face Pulls", 3, 15, 20, 45),
        ("Dumbbell Row", 3, 10, 12, 75),
        ("Lat Pulldown Close Grip", 3, 10, 12, 75),
        ("Cable Bicep Curl", 3, 12, 15, 60),
        ("Dips", 3, 1, 20, 75),
        ("Overhead Tricep Extension", 3, 12, 15, 60),
        ("Incline Walk", 1, 25, 25, 0),
    ],
    "Lower Day": [
        ("Romanian Deadlift", 4, 8, 10, 90),
        ("Smith Machine Squat", 3, 10, 12, 75),
        ("Leg Press", 3, 10, 12, 75),
        ("Walking Lunges", 3, 12, 12, 60),
        ("Leg Curl", 3, 12, 15, 60),
        ("Seated Calf Raise", 4, 15, 20, 45),
        ("Ab Cruncher", 3, 15, 20, 45),
        ("Incline Walk", 1, 25, 25, 0),
    ],
}
