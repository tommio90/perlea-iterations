#!/usr/bin/env python3
"""
Perlea Website Build-Measure-Learn Loop
20 iterations × 30-minute sleep = 10 hours
Gemini 2.0 Flash → user simulation | Codex CLI → building | Git → deploy
"""

import os, sys, json, re, time, subprocess, pty, datetime, select
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
WORKSPACE   = Path('/Users/giuseppetomasello/perlea-iterations')
ITER_DIR    = WORKSPACE / '.iterations'
STATE_FILE  = ITER_DIR / 'state.json'
LOG_FILE    = ITER_DIR / 'run.log'
MAX_ITERS   = 20
SLEEP_MIN   = 30   # minutes between iterations

# ── Personas (cycle through 4 repeatedly) ──────────────────────────────────
PERSONAS = [
    {
        "name": "Marcus",
        "role": "Founder, freight-tech startup, Series A in 11 weeks",
        "context": (
            "Extremely time-pressured. Has seen 100 landing pages. Deeply skeptical of "
            "learning platforms — burned by ones that didn't lead to shipping. Has $1.2M "
            "raised, domain expertise, but can't code. Evaluating in 30 seconds."
        ),
        "primary_concern": "Can this actually help me demo something for Series A investors in 8 weeks?",
        "tolerance": "Very low. Bounces instantly if it feels generic or slow.",
    },
    {
        "name": "Daniel",
        "role": "Senior PM at Series B SaaS, AI team",
        "context": (
            "Took 3 AI courses, shipped zero features. Cynical about learning platforms. "
            "His team lead just asked why the backlog AI feature hasn't moved in 4 months. "
            "Looking for something that leads to actually shipping."
        ),
        "primary_concern": "Will this help me stop being a passenger in my own product?",
        "tolerance": "Medium. Will read if it speaks to his specific frustration.",
    },
    {
        "name": "Sarah",
        "role": "Senior frontend dev (React/TS), wants to add AI to her skillset",
        "context": (
            "Technically confident. Hates being talked down to. Tried tutorials that broke "
            "the moment her real use-case differed from the example. Looking for something "
            "that respects her existing skills."
        ),
        "primary_concern": "Can I build with my existing stack or will this force me to start from scratch?",
        "tolerance": "High if technical, low if it feels like a bootcamp.",
    },
    {
        "name": "Priya",
        "role": "Senior management consultant, advises Fortune 500 on AI strategy",
        "context": (
            "Non-technical but brilliant. Needs to stop relying on engineers to build PoCs "
            "for client proposals. Has budget. Needs credibility through artifacts, not slides. "
            "Evaluates everything on: 'can I show this to a CTO?'"
        ),
        "primary_concern": "Can I build PoCs without becoming an engineer?",
        "tolerance": "Medium. Will stay if value is clear and results are visible fast.",
    },
]

# ── Helpers ─────────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    ITER_DIR.mkdir(exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"iteration": 0, "history": []}

def save_state(state):
    ITER_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

def get_html():
    html = (WORKSPACE / 'index.html').read_text()
    # Truncate to ~12k chars for the API (skip the WebGL shader blob)
    if len(html) > 14000:
        # Keep head + first ~8k + last 2k
        head = html[:8000]
        tail = html[-3000:]
        return head + "\n\n... [SHADER CODE TRUNCATED] ...\n\n" + tail
    return html

def call_gemini(prompt, key):
    import urllib.request
    url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}'
    body = {
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'temperature': 0.7, 'maxOutputTokens': 2048}
    }
    import json as j
    req = urllib.request.Request(
        url,
        data=j.dumps(body).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    resp = urllib.request.urlopen(req, timeout=30)
    data = j.loads(resp.read())
    return data['candidates'][0]['content']['parts'][0]['text']

def simulate_user(html, persona, gemini_key):
    prompt = f"""You are a UX researcher simulating a real user visiting a website.

PERSONA: {persona['name']}
ROLE: {persona['role']}
CONTEXT: {persona['context']}
PRIMARY CONCERN: {persona['primary_concern']}
TOLERANCE: {persona['tolerance']}

You've just opened the Perlea AI landing page. Read the HTML below and give honest, specific feedback as this person.

Return ONLY valid JSON in exactly this format (no markdown, no extra text):
{{
  "first_3_seconds": "what you noticed immediately (1-2 sentences)",
  "headline_reaction": "your honest reaction to the main headline",
  "clarity_score": 1-10,
  "conversion_likelihood": 1-10,
  "what_made_you_stay": "1 thing that kept you reading (or 'nothing')",
  "what_confused_you": ["up to 3 specific confusing elements"],
  "missing_info": ["up to 3 things you looked for but couldn't find"],
  "drop_off_reason": "the single most likely reason you'd leave without signing up",
  "copy_improvements": [
    {{"location": "section name or selector", "current": "current copy", "improved": "your improved version", "why": "brief reason"}}
  ],
  "structural_improvements": [
    {{"what": "what to change", "why": "why it would help conversion"}}
  ],
  "overall_rating": 1-10,
  "verdict": "your brutally honest one-line verdict"
}}

HTML:
{html}"""

    raw = call_gemini(prompt, gemini_key)

    # Extract JSON
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Fallback: return a minimal structure
    return {
        "first_3_seconds": "Could not parse structured response",
        "overall_rating": 5,
        "verdict": raw[:200],
        "copy_improvements": [],
        "structural_improvements": [],
        "drop_off_reason": "Unknown",
        "missing_info": [],
        "what_confused_you": [],
        "clarity_score": 5,
        "conversion_likelihood": 5,
        "what_made_you_stay": "Unknown",
        "headline_reaction": "Unknown",
    }

def generate_task(feedback, persona, iteration):
    """Build a precise Codex task from the feedback."""
    copy_changes = feedback.get('copy_improvements', [])[:4]
    struct_changes = feedback.get('structural_improvements', [])[:3]
    confused = feedback.get('what_confused_you', [])[:2]
    missing = feedback.get('missing_info', [])[:2]

    task = f"""Improve /Users/giuseppetomasello/perlea-iterations/index.html for iteration {iteration}/20.

PERSONA FEEDBACK ({persona['name']} — {persona['role']}):
Rating: {feedback.get('overall_rating','?')}/10
Conversion likelihood: {feedback.get('conversion_likelihood','?')}/10
Verdict: {feedback.get('verdict','')}
Drop-off reason: {feedback.get('drop_off_reason','')}

CONFUSING ELEMENTS (fix these):
{chr(10).join(f"- {c}" for c in confused)}

MISSING INFORMATION (add these):
{chr(10).join(f"- {m}" for m in missing)}

COPY IMPROVEMENTS (implement exactly):
{chr(10).join(f"- [{imp.get('location','')}] Change '{imp.get('current','')}' → '{imp.get('improved','')}'" for imp in copy_changes)}

STRUCTURAL IMPROVEMENTS (implement):
{chr(10).join(f"- {s.get('what','')} (because: {s.get('why','')})" for s in struct_changes)}

CONSTRAINTS (non-negotiable):
- Edit ONLY index.html
- Keep: WebGL gradient-blinds canvas, CardSwap component, #050505 bg, #FF4500 accent
- Keep: all section IDs (capabilities, who, waitlist, etc.)
- Make surgical changes only — don't rewrite sections entirely
- Commit message must start with 'iterate-{iteration:02d}:'
- After editing, run: git -C /Users/giuseppetomasello/perlea-iterations add -A && git -C /Users/giuseppetomasello/perlea-iterations commit -m "iterate-{iteration:02d}: {persona['name'].lower()} feedback (rating {feedback.get('overall_rating','?')}/10)" && git -C /Users/giuseppetomasello/perlea-iterations push origin main
"""
    return task

def run_codex_with_pty(task, iteration):
    """Run Codex CLI with PTY. Returns (success, output_str)."""
    env = {**os.environ}
    # Ensure proxy is set
    env['HTTPS_PROXY'] = 'http://152.42.177.32:8888'
    env['WSS_PROXY']   = 'http://152.42.177.32:8888'
    env['WS_PROXY']    = 'http://152.42.177.32:8888'
    env['TERM'] = 'xterm-256color'

    output_chunks = []
    exit_status = [None]

    def read_from_master(master_fd, timeout=240):
        """Read all output from the PTY master."""
        start = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                log(f"  Codex timeout after {timeout}s")
                break
            try:
                rlist, _, _ = select.select([master_fd], [], [], 1.0)
                if rlist:
                    try:
                        data = os.read(master_fd, 4096)
                        if data:
                            chunk = data.decode('utf-8', errors='replace')
                            output_chunks.append(chunk)
                            # Print progress
                            if len(output_chunks) % 20 == 0:
                                log(f"  Codex running... ({int(elapsed)}s)")
                        else:
                            break
                    except OSError:
                        break
            except (ValueError, OSError):
                break

    try:
        master_fd, slave_fd = pty.openpty()

        proc = subprocess.Popen(
            ['codex', 'exec', '--full-auto', task],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            cwd=str(WORKSPACE),
        )
        os.close(slave_fd)

        read_from_master(master_fd, timeout=300)

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

        os.close(master_fd)
        full_output = ''.join(output_chunks)
        success = proc.returncode == 0 or 'committed' in full_output.lower() or 'main' in full_output.lower()
        return success, full_output[-2000:]  # last 2k chars

    except Exception as e:
        return False, str(e)

def git_status():
    """Check if there are uncommitted changes."""
    result = subprocess.run(
        ['git', 'status', '--porcelain'],
        cwd=str(WORKSPACE), capture_output=True, text=True
    )
    return result.stdout.strip()

def git_commit_push(iteration, persona_name, rating, verdict):
    """Fallback commit + push if Codex didn't do it."""
    msg = f"iterate-{iteration:02d}: {persona_name.lower()} feedback (rating {rating}/10) — {verdict[:50]}"
    subprocess.run(['git', 'add', '-A'],          cwd=str(WORKSPACE))
    subprocess.run(['git', 'commit', '-m', msg],  cwd=str(WORKSPACE))
    subprocess.run(['git', 'push', 'origin', 'main'], cwd=str(WORKSPACE))

def notify_telegram(msg):
    """Send a Telegram notification via OpenClaw CLI."""
    try:
        subprocess.run(
            ['openclaw', 'message', 'send', '--to', '6467958860',
             '--channel', 'telegram', '--message', msg],
            timeout=10, capture_output=True
        )
    except Exception:
        pass  # Notifications are nice-to-have

# ── Main iteration ───────────────────────────────────────────────────────────
def run_iteration(iteration, state, gemini_key):
    persona = PERSONAS[(iteration - 1) % len(PERSONAS)]

    log(f"{'='*60}")
    log(f"ITERATION {iteration}/{MAX_ITERS} — {persona['name']} ({persona['role'][:40]})")
    log(f"{'='*60}")

    # 1. Simulate user
    log(f"👤 Simulating {persona['name']}...")
    html = get_html()
    try:
        feedback = simulate_user(html, persona, gemini_key)
    except Exception as e:
        log(f"  ⚠️  Gemini error: {e} — using fallback feedback")
        feedback = {
            "overall_rating": 5,
            "verdict": f"Simulation failed: {e}",
            "drop_off_reason": "Unclear value proposition",
            "copy_improvements": [{"location": "hero headline", "current": "existing", "improved": "Make the headline more specific to founders building AI demos", "why": "specificity converts"}],
            "structural_improvements": [{"what": "Add a timer/urgency element to hero", "why": "founders respond to scarcity"}],
            "what_confused_you": ["What exactly is the first session?"],
            "missing_info": ["Pricing or access info", "Timeline to first artifact"],
            "clarity_score": 5, "conversion_likelihood": 5,
            "what_made_you_stay": "Unknown", "headline_reaction": "Unknown", "first_3_seconds": "Unknown",
        }

    rating  = feedback.get('overall_rating', '?')
    verdict = feedback.get('verdict', '')
    conv    = feedback.get('conversion_likelihood', '?')
    log(f"  Rating: {rating}/10 | Conversion: {conv}/10")
    log(f"  Verdict: {verdict[:100]}")
    log(f"  Drop-off: {feedback.get('drop_off_reason','')[:80]}")

    # Save feedback
    ITER_DIR.mkdir(exist_ok=True)
    feedback_file = ITER_DIR / f'iter-{iteration:02d}-{persona["name"].lower()}.json'
    feedback_file.write_text(json.dumps({
        "iteration": iteration,
        "timestamp": datetime.datetime.now().isoformat(),
        "persona": persona,
        "feedback": feedback,
    }, indent=2))

    # 2. Generate Codex task
    log(f"🔨 Running Codex improvements...")
    task = generate_task(feedback, persona, iteration)

    # Save task for debugging
    (ITER_DIR / f'iter-{iteration:02d}-task.txt').write_text(task)

    # 3. Run Codex
    success, codex_output = run_codex_with_pty(task, iteration)
    log(f"  Codex: {'✅' if success else '⚠️'}")

    # 4. Fallback: commit if Codex didn't (or if changes exist)
    uncommitted = git_status()
    if uncommitted:
        log(f"  Committing leftover changes...")
        git_commit_push(iteration, persona['name'], rating, verdict)
    elif not success:
        log(f"  Codex made no changes — skipping commit")

    # 5. Update state
    state['iteration'] = iteration
    state['history'].append({
        "iteration": iteration,
        "persona": persona['name'],
        "rating": rating,
        "conversion": conv,
        "verdict": verdict[:80],
        "timestamp": datetime.datetime.now().isoformat(),
    })
    save_state(state)

    # 6. Milestone notifications
    if iteration % 5 == 0 or iteration == MAX_ITERS:
        recent = state['history'][-5:]
        avg_rating = sum(h.get('rating', 0) for h in recent if isinstance(h.get('rating'), int)) / max(len(recent), 1)
        msg = (
            f"🦞 Perlea iteration {iteration}/{MAX_ITERS} complete\n"
            f"Avg rating (last 5): {avg_rating:.1f}/10\n"
            f"Latest ({persona['name']}): {rating}/10 — {verdict[:60]}\n"
            f"Live: https://perlea-iterations.vercel.app"
        )
        notify_telegram(msg)
        log(f"  📲 Telegram notification sent")

    log(f"✅ Iteration {iteration} done\n")
    return feedback

# ── Entry point ──────────────────────────────────────────────────────────────
def main():
    ITER_DIR.mkdir(exist_ok=True)

    # Get Gemini key
    gemini_key = os.environ.get('GEMINI_API_KEY') or subprocess.run(
        ['zsh', '-c', 'source ~/.zshrc 2>/dev/null && echo $GEMINI_API_KEY'],
        capture_output=True, text=True
    ).stdout.strip()

    if not gemini_key:
        log("❌ GEMINI_API_KEY not found")
        sys.exit(1)

    log(f"🚀 Starting Perlea BML loop — {MAX_ITERS} iterations × {SLEEP_MIN}min = {MAX_ITERS*SLEEP_MIN//60}h")
    log(f"Gemini key: {gemini_key[:15]}...")

    state = load_state()
    start_iter = state['iteration'] + 1

    if start_iter > MAX_ITERS:
        log(f"✅ All {MAX_ITERS} iterations already complete. Check .iterations/ for results.")
        sys.exit(0)

    log(f"Starting from iteration {start_iter}")

    for iteration in range(start_iter, MAX_ITERS + 1):
        run_iteration(iteration, state, gemini_key)

        if iteration < MAX_ITERS:
            next_time = (datetime.datetime.now() + datetime.timedelta(minutes=SLEEP_MIN)).strftime('%H:%M')
            log(f"💤 Sleeping {SLEEP_MIN} minutes... next iteration at ~{next_time}")
            time.sleep(SLEEP_MIN * 60)

    # Final summary
    history = state.get('history', [])
    ratings = [h['rating'] for h in history if isinstance(h.get('rating'), int)]
    avg = sum(ratings) / len(ratings) if ratings else 0

    summary = (
        f"\n{'='*60}\n"
        f"🏁 BML LOOP COMPLETE — {MAX_ITERS} ITERATIONS\n"
        f"Duration: ~{MAX_ITERS * SLEEP_MIN // 60} hours\n"
        f"Average rating progression:\n"
    )
    for i, h in enumerate(history):
        summary += f"  {i+1:02d}. {h.get('persona','?'):8s} → {h.get('rating','?')}/10 — {h.get('verdict','')[:50]}\n"
    summary += f"\nFinal average: {avg:.1f}/10\n"
    summary += f"Live: https://perlea-iterations.vercel.app\n"
    summary += f"{'='*60}\n"

    log(summary)
    (ITER_DIR / 'summary.txt').write_text(summary)

    notify_telegram(
        f"🦞 Perlea BML complete! {MAX_ITERS} iterations done.\n"
        f"Average rating: {avg:.1f}/10\n"
        f"https://perlea-iterations.vercel.app"
    )

if __name__ == '__main__':
    main()
