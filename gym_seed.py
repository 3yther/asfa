"""Seed data for the gym tracker: exercise library, routine templates, and the
exercise list for each routine. Imported by ``database.init_gym_data`` to
populate the ``gym_*`` tables on boot (idempotent — existing rows are left
alone). This module is pure data with no DB or app imports.
"""
from collections import namedtuple

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
        "name": "Pec Deck",
        "muscle_group": "chest",
        "secondary_muscles": [],
        "equipment": "machine",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=Z57CtFmRMxA",
        "instructions": "1. Sit with your back flat against the pad\n2. Grip the handles at shoulder height, elbows soft\n3. Bring the pads together in a smooth arc\n4. Squeeze the chest hard at the centre\n5. Return slowly under control — keep tension",
        "tips": "Keep a slight bend in the elbows throughout. Drive with the chest, not the arms.",
        "rank_bronze": 20, "rank_silver": 35, "rank_gold": 50, "rank_platinum": 65, "rank_diamond": 80
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
        "youtube_url": "https://www.youtube.com/watch?v=f_OGBg2KxgY",
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
        "name": "Seated Triceps Press",
        "muscle_group": "triceps",
        "secondary_muscles": ["chest", "shoulders"],
        "equipment": "machine",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=0kBrPqjZQ5s",
        "instructions": "1. Sit with your back against the pad\n2. Grip the handles at shoulder height\n3. Press down and forward until the arms extend\n4. Squeeze the triceps at the bottom\n5. Return slowly under control — don't let the stack slam",
        "tips": "Keep elbows tucked and shoulders down. Don't lock out aggressively.",
        "rank_bronze": 15, "rank_silver": 30, "rank_gold": 45, "rank_platinum": 60, "rank_diamond": 75
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
        "youtube_url": "https://www.youtube.com/watch?v=O94yEoGXtBY",
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
        "youtube_url": "https://www.youtube.com/watch?v=baEXLy09Ncc",
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
        "youtube_url": "https://www.youtube.com/watch?v=AHnX-aimA4E",
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
        "youtube_url": "https://www.youtube.com/watch?v=b8DqTO6ak0k",
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
        "youtube_url": "https://www.youtube.com/watch?v=xUm0BiZCWlQ",
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
        "youtube_url": "https://www.youtube.com/watch?v=NAsObfFJXvE",
        "instructions": "1. Set treadmill to speed 3.5\n2. Set incline to 13\n3. Walk for 25 minutes\n4. Hold sides only if necessary\n5. Keep upright posture",
        "tips": "Don't hold the rails — it reduces calorie burn significantly. Swing arms naturally.",
        "rank_bronze": 10, "rank_silver": 20, "rank_gold": 30, "rank_platinum": 45, "rank_diamond": 60
    },
    # ── EQUIPMENT EXPANSION — extra movements for the home/commercial gym setup ──
    # CHEST
    {
        "name": "Dumbbell Bench Press",
        "muscle_group": "chest",
        "secondary_muscles": ["triceps", "shoulders"],
        "equipment": "dumbbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=KRbyo0jj2ao",
        "instructions": "1. Lie flat on bench, a dumbbell in each hand at chest height\n2. Wrists stacked over elbows, feet flat on floor\n3. Press dumbbells up and slightly together\n4. Lower with control until you feel a chest stretch\n5. Keep shoulder blades pinned back throughout",
        "tips": "Greater range of motion than the barbell. Don't clash the dumbbells at the top — keep tension on the chest.",
        "rank_bronze": 12, "rank_silver": 18, "rank_gold": 24, "rank_platinum": 32, "rank_diamond": 40
    },
    {
        "name": "Push-ups",
        "muscle_group": "chest",
        "secondary_muscles": ["triceps", "shoulders", "core"],
        "equipment": "bodyweight",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=Zi6c09DRGxk",
        "instructions": "1. Hands slightly wider than shoulders, body in a straight plank\n2. Brace core and squeeze glutes\n3. Lower until chest nearly touches the floor\n4. Keep elbows at roughly 45 degrees, not flared\n5. Press back up to full lockout",
        "tips": "Body stays a rigid line — no sagging hips. Full range beats fast half-reps. Elevate feet to make it harder.",
        "rank_bronze": 10, "rank_silver": 20, "rank_gold": 30, "rank_platinum": 45, "rank_diamond": 60
    },
    # BACK
    {
        "name": "Barbell Deadlift",
        "muscle_group": "back",
        "secondary_muscles": ["hamstrings", "glutes", "core"],
        "equipment": "barbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=Y1IGeJEXpF4",
        "instructions": "1. Bar over mid-foot, shins close\n2. Hinge and grip just outside knees\n3. Chest up, flatten back, take slack out of the bar\n4. Drive through the floor, standing tall with hips and knees together\n5. Lower under control, hips back first",
        "tips": "Neutral spine — never round the lower back. Push the floor away rather than pulling with the arms. Reset each rep.",
        "rank_bronze": 60, "rank_silver": 100, "rank_gold": 140, "rank_platinum": 180, "rank_diamond": 220
    },
    {
        "name": "Weighted Pull-ups",
        "muscle_group": "back",
        "secondary_muscles": ["biceps", "core"],
        "equipment": "bodyweight",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=KOw6VhY8McQ",
        "instructions": "1. Add weight via dip belt or a dumbbell held between the feet\n2. Grip bar slightly wider than shoulders, hang fully\n3. Pull up until chin clears the bar\n4. Squeeze lats at the top\n5. Lower slowly to a full dead hang",
        "tips": "Only add weight once you own 8+ clean bodyweight reps. Rank weight = load added, not bodyweight. Strict form over heavy plates.",
        "rank_bronze": 0, "rank_silver": 10, "rank_gold": 20, "rank_platinum": 30, "rank_diamond": 40
    },
    # LEGS
    {
        "name": "45 Degree Leg Press",
        "muscle_group": "quads",
        "secondary_muscles": ["hamstrings", "glutes"],
        "equipment": "machine",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=q4W4_VJbKW0",
        "instructions": "1. Sit in the 45-degree sled, back and hips flat on the pad\n2. Feet shoulder width, mid-platform\n3. Release the safeties and lower until knees reach ~90 degrees\n4. Drive through heels to extend — don't lock the knees\n5. Keep lower back glued to the pad the whole time",
        "tips": "Rank weight is plates loaded (excludes sled). If your lower back peels off the pad you're going too deep or too heavy.",
        "rank_bronze": 80, "rank_silver": 130, "rank_gold": 180, "rank_platinum": 230, "rank_diamond": 280
    },
    {
        "name": "Dumbbell Goblet Squat",
        "muscle_group": "quads",
        "secondary_muscles": ["glutes", "core"],
        "equipment": "dumbbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=k_EhLGvM8TQ",
        "instructions": "1. Hold one dumbbell vertically against your chest, both hands under the top plate\n2. Feet shoulder width, toes slightly out\n3. Squat down keeping torso upright and elbows inside knees\n4. Descend until thighs are at least parallel\n5. Drive through heels to stand tall",
        "tips": "The front-loaded weight keeps you upright — great for learning squat depth. Keep the dumbbell tight to your chest.",
        "rank_bronze": 12, "rank_silver": 20, "rank_gold": 30, "rank_platinum": 40, "rank_diamond": 50
    },
    {
        "name": "Barbell Hack Squat",
        "muscle_group": "quads",
        "secondary_muscles": ["hamstrings", "glutes"],
        "equipment": "barbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=EdtaJRBqwes",
        "instructions": "1. Stand with the barbell on the floor behind your heels\n2. Feet shoulder width, squat down and grip the bar behind you\n3. Chest up, back flat\n4. Drive through heels to stand, bar tracking up the back of the legs\n5. Lower under control back to the floor",
        "tips": "Emphasises the quads (especially the sweep) more than a back squat. Use straps if grip fails before the legs do.",
        "rank_bronze": 40, "rank_silver": 60, "rank_gold": 80, "rank_platinum": 105, "rank_diamond": 130
    },
    # SHOULDERS
    {
        "name": "Barbell Shoulder Press",
        "muscle_group": "shoulders",
        "secondary_muscles": ["triceps", "core"],
        "equipment": "barbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=F3QY5vMz_6I",
        "instructions": "1. Bar racked at collarbone height, grip just outside shoulders\n2. Brace core and squeeze glutes, elbows slightly in front of the bar\n3. Press straight up, moving your head back slightly to clear the bar\n4. Lock out overhead with the bar over mid-foot\n5. Lower under control to the front rack",
        "tips": "Squeeze glutes to stop the lower back arching. Bar path is a straight vertical line, not a forward arc.",
        "rank_bronze": 30, "rank_silver": 45, "rank_gold": 60, "rank_platinum": 75, "rank_diamond": 90
    },
    # ARMS
    {
        "name": "Barbell Curl",
        "muscle_group": "biceps",
        "secondary_muscles": ["forearms"],
        "equipment": "barbell",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=JJB8XgKltA8",
        "instructions": "1. Stand tall, shoulder-width grip on the barbell, palms up\n2. Elbows pinned to your sides\n3. Curl the bar up, supinating hard at the top\n4. Squeeze the biceps at the top\n5. Lower slowly to full extension",
        "tips": "Keep the torso still — no swinging or leaning back. Only the forearms move. Control the negative.",
        "rank_bronze": 20, "rank_silver": 30, "rank_gold": 40, "rank_platinum": 50, "rank_diamond": 60
    },
    {
        "name": "Dumbbell Curls",
        "muscle_group": "biceps",
        "secondary_muscles": ["forearms"],
        "equipment": "dumbbell",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=6DeLZ6cbgWQ",
        "instructions": "1. Stand with a dumbbell in each hand, arms at your sides, palms facing in\n2. Curl up, rotating the palm to face the shoulder as you rise\n3. Squeeze the biceps hard at the top\n4. Lower slowly, rotating back to neutral\n5. Keep elbows pinned to your sides throughout",
        "tips": "Supinating (twisting the palm up) maximises the peak contraction. Don't swing — keep tension the whole rep.",
        "rank_bronze": 8, "rank_silver": 12, "rank_gold": 16, "rank_platinum": 22, "rank_diamond": 28
    },
    {
        "name": "Cable Tricep Pushdown",
        "muscle_group": "triceps",
        "secondary_muscles": [],
        "equipment": "cable",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=-zLyUAo1gMw",
        "instructions": "1. Set cable high with a straight bar attachment\n2. Grip overhand, shoulder-width, elbows pinned to sides\n3. Push the bar down to full lockout\n4. Squeeze the triceps at the bottom\n5. Return slowly, letting the bar rise to about chest height",
        "tips": "Only the forearms move — keep elbows glued to your sides. Straight bar hits the triceps a little differently to the rope.",
        "rank_bronze": 15, "rank_silver": 25, "rank_gold": 35, "rank_platinum": 45, "rank_diamond": 55
    },
    # ── CORE + BIKE DAY — the Wednesday session ──────────────────────────────
    # Mostly bodyweight, so the rank thresholds are *added* load (a plate held or
    # a belt), not total weight. 0 added weight is a legitimate Bronze.
    {
        "name": "Ab Wheel Rollout",
        "muscle_group": "core",
        "secondary_muscles": ["shoulders", "back"],
        "equipment": "bodyweight",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=rqiTPdK1c_I",
        "instructions": "1. Kneel with the wheel under your shoulders\n2. Brace the core and tuck the pelvis\n3. Roll out as far as you can hold a flat back\n4. Pull back with the abs, not the arms\n5. Stop short of any lower-back sag",
        "tips": "Range is earned — shorten the rollout rather than letting the hips drop. Breathe out on the way back.",
        "rank_bronze": 0, "rank_silver": 5, "rank_gold": 10, "rank_platinum": 15, "rank_diamond": 20
    },
    {
        "name": "Hanging Leg Raise",
        "muscle_group": "core",
        "secondary_muscles": ["forearms"],
        "equipment": "bodyweight",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=Pr1ieGZ5atk",
        "instructions": "1. Hang from the bar, shoulders active\n2. Raise the legs with the abs, knees soft\n3. Lift to hip height or higher\n4. Lower slowly — no swinging\n5. Reset the hang before the next rep",
        "tips": "If you swing, you're using momentum. Bend the knees to make it easier rather than kipping.",
        "rank_bronze": 0, "rank_silver": 5, "rank_gold": 10, "rank_platinum": 15, "rank_diamond": 20
    },
    {
        "name": "Pallof Press",
        "muscle_group": "core",
        "secondary_muscles": ["shoulders"],
        "equipment": "cable",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=AH_QZLm_0-s",
        "instructions": "1. Set the cable at chest height, stand side-on\n2. Hold the handle at the sternum with both hands\n3. Press straight out and resist the rotation\n4. Hold 2 seconds at full extension\n5. Return under control, then switch sides",
        "tips": "An anti-rotation hold, not a chest press — the work is in not twisting. Do both sides every set.",
        "rank_bronze": 5, "rank_silver": 10, "rank_gold": 20, "rank_platinum": 30, "rank_diamond": 40
    },
    {
        "name": "Plank",
        "muscle_group": "core",
        "secondary_muscles": ["shoulders"],
        "equipment": "bodyweight",
        "exercise_type": "isolation",
        "youtube_url": "https://www.youtube.com/watch?v=pSHjTRCQxIw",
        "instructions": "1. Forearms under the shoulders, feet hip-width\n2. Squeeze glutes and tuck the pelvis\n3. Hold a straight line from head to heels\n4. Breathe normally throughout\n5. Stop the set when the hips start to sag",
        "tips": "Logged in seconds, not reps. Add a plate on the back before adding time past 60 seconds.",
        "rank_bronze": 0, "rank_silver": 5, "rank_gold": 10, "rank_platinum": 15, "rank_diamond": 20
    },
    {
        "name": "Cycling",
        "muscle_group": "cardio",
        "secondary_muscles": [],
        "equipment": "bike",
        "exercise_type": "cardio",
        "youtube_url": "https://www.youtube.com/watch?v=nJ5nB4rwLcE",
        "instructions": "1. Set the saddle so the knee is slightly bent at the bottom\n2. Ride 20-30 minutes at a steady, conversational effort\n3. Keep cadence around 80-90 rpm\n4. Raise resistance rather than mashing a heavy gear slowly\n5. Cool down for the last 2 minutes",
        "tips": "This is the stamina day — steady state, not intervals. Effort should stay talkable.",
        "rank_bronze": 10, "rank_silver": 20, "rank_gold": 30, "rank_platinum": 45, "rank_diamond": 60
    },
    {
        "name": "Hip Thrust",
        "muscle_group": "glutes",
        "secondary_muscles": ["hamstrings", "quads"],
        "equipment": "barbell",
        "exercise_type": "compound",
        "youtube_url": "https://www.youtube.com/watch?v=xDmFkJxPzeM",
        "instructions": "1. Sit with upper back on a bench, bar across the hips\n2. Plant feet flat, shin vertical at the top\n3. Drive through the heels and extend the hips\n4. Squeeze the glutes hard at lockout, ribs down\n5. Lower under control — don't dump the weight",
        "tips": "Full hip extension, not a lower-back arch. A pad on the bar saves the hips. Glute Bridge is the floor variation.",
        "rank_bronze": 40, "rank_silver": 80, "rank_gold": 120, "rank_platinum": 160, "rank_diamond": 200
    },
]

# ── The live Sept 1-15 upper/lower/arms split ─────────────────────────────────
# Calendar-anchored (not a rolling cycle): Tue Upper A (heavy), Wed Lower A, Fri
# Upper B (light/endurance), Sat Lower B, Sun Arms + Shoulders. Mon + Thu are
# rest. Each trained day carries a distinct day_type so the up-next rotation in
# gym.js cycles through all five (upper_a → lower_a → upper_b → lower_b → arms).
# Rest days are not routines — there is nothing to log — so they live in gym.js's
# WEEK_SCHEDULE only.
#
# This list is the single source of truth for the split: database.seed_gym_routines
# rewrites any routine that drifts from it, and database.PLAN_SESSIONS renders the
# same slots on /gym/plan. Order here IS the logging order (see LOCKED below).
ROUTINES = [
    {"name": "Upper A · Tuesday", "day_type": "upper_a", "description": "Heavy upper — bench 5×5", "order_index": 0},
    {"name": "Lower A · Wednesday", "day_type": "lower_a", "description": "Squat-focused legs", "order_index": 1},
    {"name": "Upper B · Friday", "day_type": "upper_b", "description": "Light/endurance upper", "order_index": 2},
    {"name": "Lower B · Saturday", "day_type": "lower_b", "description": "Leg press & posterior chain", "order_index": 3},
    {"name": "Arms + Shoulders · Sunday", "day_type": "arms", "description": "Delts & arms", "order_index": 4},
]

# Exercise order within a day is fixed and must not drift — the seed is
# authoritative and re-imposes it on every boot (see database.seed_gym_routines).
LOCKED = True

# One prescribed exercise slot in a routine.
#   sets/rep_min/rep_max/rest  — what the logger pre-fills
#   weight                     — the prescribed working weight in kg (None = bodyweight
#                                or "whatever you're on"); shown as the target, never
#                                as a logged value
#   notes                      — warm-up ramp or execution cue, shown under the slot
#   prescription               — display override for slots that reps can't express
#                                (holds, time, to-failure); auto-generated when None
Slot = namedtuple(
    "Slot", "exercise sets rep_min rep_max rest weight notes prescription")
Slot.__new__.__defaults__ = (None, None, None)   # weight, notes, prescription


def describe(slot) -> str:
    """The human prescription for a slot — "60kg × 5×5 (warm-up …)". Used by the
    /gym/plan week grid so the plan page and the logger can't disagree."""
    if slot.prescription:
        base = slot.prescription
    else:
        reps = (str(slot.rep_min) if slot.rep_min == slot.rep_max
                else f"{slot.rep_min}–{slot.rep_max}")
        base = f"{slot.sets}×{reps}"
        if slot.weight:
            base = f"{slot.weight:g}kg × {base}"
    return f"{base} ({slot.notes})" if slot.notes else base


ROUTINE_EXERCISES = {
    # Tue — Upper A, HEAVY. 20 sets → 50 min target. Bench leads on the 5×5.
    "Upper A · Tuesday": [
        Slot("Barbell Bench Press", 5, 5, 5, 150, weight=65,
             notes="warm-up 20×5, 40×3, 50×2"),
        Slot("Pec Deck", 2, 8, 10, 75),
        Slot("Lat Pulldown", 3, 8, 8, 90, weight=73),
        Slot("Seated Cable Row", 3, 8, 8, 75, weight=66),
        Slot("Cable Lateral Raises", 2, 10, 12, 60),
        Slot("Cable Tricep Pushdown", 2, 8, 8, 60),
        Slot("Dumbbell Curls", 3, 8, 10, 60),
    ],
    # Wed — Lower A. 12 sets → 30 min target. Squat-focused.
    "Lower A · Wednesday": [
        Slot("Barbell Squat", 3, 6, 8, 150, notes="or Leg Press"),
        Slot("Leg Curl", 3, 8, 10, 90, notes="or Romanian Deadlift"),
        Slot("Leg Extension", 3, 10, 12, 75),
        Slot("Standing Calf Raise", 3, 12, 15, 60),
    ],
    # Fri — Upper B, LIGHT/ENDURANCE. 11 sets → 28 min target. Same bench bar,
    # lighter 3×8 scheme.
    "Upper B · Friday": [
        Slot("Barbell Bench Press", 3, 8, 8, 120, weight=65),
        Slot("Lat Pulldown", 3, 8, 8, 90, weight=73),
        Slot("Seated Cable Row", 3, 8, 8, 75, weight=66),
        Slot("Pec Deck", 2, 8, 8, 75),
    ],
    # Sat — Lower B. 12 sets → 30 min target. Leg press & posterior chain.
    "Lower B · Saturday": [
        Slot("Leg Press", 3, 8, 10, 120),
        Slot("Walking Lunges", 3, 8, 8, 90, prescription="3×8 each leg",
             notes="or Bulgarian Split Squats"),
        Slot("Leg Curl", 3, 10, 12, 75, notes="hamstring curl"),
        Slot("Hip Thrust", 3, 12, 12, 90, notes="or Glute Bridge"),
    ],
    # Sun — Arms + Shoulders. 18 sets → 45 min target. Weekly weigh-in day.
    "Arms + Shoulders · Sunday": [
        Slot("Seated Dumbbell Shoulder Press", 3, 10, 10, 75),
        Slot("Lateral Raises", 3, 10, 12, 60),
        Slot("Cable Tricep Pushdown", 3, 8, 8, 60),
        Slot("Seated Triceps Press", 3, 8, 8, 60),
        Slot("Barbell Curl", 3, 8, 10, 60, notes="or EZ-Bar Curl"),
        Slot("Hammer Curls", 3, 8, 10, 60),
    ],
}
