from threading import RLock
from functools import wraps
import time
import random
from flask import Flask, request, jsonify, render_template_string
import chess
ENGINE_LOCK = RLock()

def serialized(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with ENGINE_LOCK:
            return fn(*args, **kwargs)
    return wrapper
ENGINE_NAME = "checkMATE"
app = Flask(__name__)

MATE = 100000
VALUES = {chess.PAWN:100, chess.KNIGHT:320, chess.BISHOP:335, chess.ROOK:500, chess.QUEEN:900, chess.KING:0}
DEPTHS = {"easy": 1, "normal": 2, "hard": 3, "extreme": 4}
# Tiny optional starter course. Moves are SAN and are validated as the course runs.
LESSONS = [
    {"title":"Lesson 1: Control the centre", "fen":chess.STARTING_FEN,
     "prompt":"Play e4 or d4. Central pawns help control important squares and open lines for your pieces.", "answers":["e4","d4"]},
    {"title":"Lesson 2: Develop a knight", "fen":chess.STARTING_FEN,
     "prompt":"Move a knight toward the centre. Nf3 or Nc3 are both good developing moves.", "answers":["Nf3","Nc3"]},
    {"title":"Lesson 3: Find a safe move", "fen":"rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
     "prompt":"Black played ...e5. Develop a piece or protect your centre. Nf3 is a strong natural choice.", "answers":["Nf3","Bc4","Nc3"]},
]


def piece_square_bonus(piece_type, square, color):
    file_ = chess.square_file(square)
    rank = chess.square_rank(square)
    if color == chess.BLACK:
        rank = 7 - rank
    center = 7 - (abs(2 * file_ - 7) + abs(2 * rank - 7)) // 2
    if piece_type == chess.PAWN: return rank * 7 + center * 2
    if piece_type == chess.KNIGHT: return center * 9
    if piece_type == chess.BISHOP: return center * 4
    if piece_type == chess.ROOK: return rank * 2
    if piece_type == chess.QUEEN: return center * 2
    if piece_type == chess.KING: return -center * 3 if rank < 3 else center * 2
    return 0


def evaluate(board):
    if board.is_checkmate(): return -MATE if board.turn == chess.WHITE else MATE
    if board.is_stalemate() or board.is_insufficient_material() or board.can_claim_fifty_moves() or board.can_claim_threefold_repetition(): return 0
    score = 0
    for square, piece in board.piece_map().items():
        value = VALUES[piece.piece_type] + piece_square_bonus(piece.piece_type, square, piece.color)
        score += value if piece.color == chess.WHITE else -value
    mobility = board.legal_moves.count()
    score += mobility * 2 if board.turn == chess.WHITE else -mobility * 2
    return score if board.turn == chess.WHITE else -score


def move_order_score(board, move):
    score = 0
    if board.is_capture(move):
        victim = board.piece_at(move.to_square)
        attacker = board.piece_at(move.from_square)
        score += 10 * (VALUES[victim.piece_type] if victim else VALUES[chess.PAWN]) - (VALUES[attacker.piece_type] if attacker else 0)
    if move.promotion: score += VALUES[move.promotion]
    if board.gives_check(move): score += 50
    return score


def negamax(board, depth, alpha, beta, ply=0):
    if board.is_checkmate(): return -MATE + ply, None
    if board.is_stalemate() or board.is_insufficient_material() or board.can_claim_fifty_moves() or board.can_claim_threefold_repetition(): return 0, None
    if depth <= 0: return evaluate(board), None
    moves = list(board.legal_moves)
    if not moves: return evaluate(board), None
    moves.sort(key=lambda m: move_order_score(board, m), reverse=True)
    best_move, best_score = None, -MATE * 2
    for move in moves:
        board.push(move)
        score, _ = negamax(board, depth - 1, -beta, -alpha, ply + 1)
        score = -score
        board.pop()
        if score > best_score: best_score, best_move = score, move
        alpha = max(alpha, score)
        if alpha >= beta: break
    return best_score, best_move


def choose_move(board, difficulty):
    depth = DEPTHS.get(difficulty, 2)
    if difficulty == "easy" and random.random() < 0.25:
        return random.choice(list(board.legal_moves)) if board.legal_moves.count() else None
    started = time.time()
    best = None
    for d in range(1, depth + 1):
        score, move = negamax(board, d, -MATE * 2, MATE * 2)
        if move is not None: best = move
        if time.time() - started > (0.5 if difficulty == "extreme" else 0.25): break
    return best


def get_game():
    # One shared local game for a single-player local app.
    if not hasattr(app, "game"):
        app.game = {"board": chess.Board(), "human": chess.WHITE, "difficulty":"normal", "moves":[], "review":[]}
    return app.game


def status_text(board):
    if board.is_checkmate(): return "Checkmate! " + ("Black" if board.turn == chess.WHITE else "White") + " wins."
    if board.is_stalemate(): return "Stalemate — draw."
    if board.is_insufficient_material(): return "Draw by insufficient material."
    if board.can_claim_threefold_repetition(): return "Draw is claimable by repetition."
    if board.can_claim_fifty_moves(): return "Draw is claimable by the 50-move rule."
    return ("White" if board.turn else "Black") + " to move" + (" — check!" if board.is_check() else "")


def board_payload(game):
    b = game["board"]
    return {"fen": b.fen(), "turn": "white" if b.turn else "black", "human": "white" if game["human"] else "black",
            "difficulty": game["difficulty"], "moves": game["moves"], "status": status_text(b),
            "game_over": b.is_game_over(claim_draw=True), "result": b.result(claim_draw=True) if b.is_game_over(claim_draw=True) else None,
            "last_move": game["moves"][-1] if game["moves"] else None}


@app.get("/")
def home():
    return render_template_string(HTML)

@app.get("/api/state")
def state():
    return jsonify(board_payload(get_game()))

@app.post("/api/new")
def new_game():
    data = request.get_json(force=True)
    game = get_game()
    game["board"] = chess.Board()
    game["human"] = chess.BLACK if data.get("color") == "black" else chess.WHITE
    game["difficulty"] = data.get("difficulty", "normal") if data.get("difficulty") in DEPTHS else "normal"
    game["moves"] = []
    game["review"] = []
    # If human chooses black, let engine play White first.
    if game["human"] == chess.BLACK:
        m = choose_move(game["board"], game["difficulty"])
        if m:
            san = game["board"].san(m); game["board"].push(m); game["moves"].append({"ply":1,"san":san,"uci":m.uci(),"color":"white"})
    return jsonify(board_payload(game))
                      
@app.post("/api/move")
@serialized
def user_move():
    game = get_game(); b = game["board"]
    data = request.get_json(force=True)
    try: move = chess.Move.from_uci(data.get("uci", ""))
    except Exception: return jsonify({"error":"Invalid move."}), 400
    if b.is_game_over(claim_draw=True): return jsonify({"error":"The game is over. Start a new game."}), 400
    if b.turn != game["human"]: return jsonify({"error":"It is the engine's turn."}), 400
    if move not in b.legal_moves: return jsonify({"error":"Illegal move."}), 400
    before = b.copy(); san = b.san(move); b.push(move)
    game["moves"].append({"ply":len(game["moves"])+1,"san":san,"uci":move.uci(),"color":"white" if before.turn else "black"})
    if not b.is_game_over(claim_draw=True):
        reply = choose_move(b, game["difficulty"])
        if reply:
            san = b.san(reply); color = "white" if b.turn else "black"; b.push(reply)
            game["moves"].append({"ply":len(game["moves"])+1,"san":san,"uci":reply.uci(),"color":color})
    payload = board_payload(game)
    if b.is_game_over(claim_draw=True): payload["review"] = make_review(game)
    return jsonify(payload)

@app.post("/api/training/check")
def training_check():
    data = request.get_json(force=True); idx = int(data.get("lesson", 0)); uci = data.get("uci", "")
    if idx < 0 or idx >= len(LESSONS): return jsonify({"correct":False,"message":"Lesson not found."}), 400
    lesson = LESSONS[idx]; b = chess.Board(lesson["fen"])
    try: move = chess.Move.from_uci(uci)
    except Exception: return jsonify({"correct":False,"message":"Choose a legal move on the board."})
    if move not in b.legal_moves: return jsonify({"correct":False,"message":"That move is illegal. Try another square."})
    san = b.san(move)
    correct = san in lesson["answers"]
    return jsonify({"correct":correct,"message":("Correct! " if correct else "Legal move, but try a move that better matches the lesson. ") + lesson["prompt"], "san":san})

@app.get("/api/lesson/<int:idx>")
def lesson(idx):
    if idx < 0 or idx >= len(LESSONS): return jsonify({"error":"Lesson not found."}), 404
    l = LESSONS[idx]
    return jsonify({"index":idx,"title":l["title"],"fen":l["fen"],"prompt":l["prompt"],"answers":l["answers"]})


def make_review(game):
    b = game["board"]
    human_name = "White" if game["human"] else "Black"
    result = b.result(claim_draw=True)
    tips = []
    if b.is_stalemate():
        tips.append("Stalemate: the side to move had no legal moves but was not in check. When winning, leave the opponent at least one legal move unless you can checkmate.")
    elif b.is_checkmate():
        winner = "Black" if b.turn == chess.WHITE else "White"
        if winner == human_name:
            tips.append("Nice finish! Review the final attack and look for similar mating patterns in your next game.")
        else:
            tips.append("Review the final position: identify the checks and threats that made checkmate unavoidable. Practise king safety and checking for opponent threats each turn.")
    elif result == "1/2-1/2":
        tips.append("The game was drawn. Review whether you had a chance to improve your position or convert an advantage into a win.")
    elif (result == "1-0" and game["human"] == chess.WHITE) or (result == "0-1" and game["human"] == chess.BLACK):
        tips.append("You won. Review the game for missed tactics too; winning games can still reveal habits to improve.")
    else:
        tips.append("After a loss, review the first moment you lost material or king safety. Before each move, check: checks, captures, threats — for both sides.")
    tips.append("Quick habit: after your opponent moves, ask what their move attacks before choosing your reply.")
    return {"result":result,"tips":tips,"moves":game["moves"]}

HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"><title>checkMATE Chess</title>
<style>
:root{color-scheme:dark;--bg:#10151e;--panel:#1b2431;--text:#f3f6fa;--muted:#aeb9c8;--accent:#72d6b2;--light:#e8dfc9;--dark:#6f8d83;--sel:#f6d365}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif}main{max-width:760px;margin:auto;padding:16px}h1{margin:4px 0;font-size:30px;letter-spacing:.5px}p{color:var(--muted);line-height:1.45}.panel{background:var(--panel);border:1px solid #303b4b;border-radius:16px;padding:14px;margin:12px 0}.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center}button,select{font:inherit;border-radius:10px;border:1px solid #465467;background:#273447;color:var(--text);padding:10px 12px;min-height:42px}button{cursor:pointer}button.primary{background:var(--accent);color:#10231e;border-color:var(--accent);font-weight:700}button.active{outline:2px solid var(--accent)}select{flex:1;min-width:120px}.board{width:min(92vw,560px);aspect-ratio:1;display:grid;grid-template-columns:repeat(8,1fr);margin:12px auto;border:4px solid #273447;border-radius:4px;overflow:hidden;touch-action:manipulation}.sq{border:0;border-radius:0;padding:0;min-height:0;aspect-ratio:1;display:flex;align-items:center;justify-content:center;font-size:clamp(26px,8.5vw,54px);line-height:1;position:relative;color:#10151e}.sq.light{background:var(--light)}.sq.dark{background:var(--dark)}.sq.blackpiece{color:#131820;text-shadow:0 1px 0 #fff8}.sq.whitepiece{color:#fffdf4;text-shadow:0 1px 2px #17202b,0 0 2px #17202b}.sq.selected{box-shadow:inset 0 0 0 4px var(--sel)}.sq.target:after{content:'';position:absolute;width:22%;height:22%;border-radius:50%;background:#173d31aa}.sq.capture:after{content:'';position:absolute;inset:4%;border:4px solid #173d31aa;border-radius:50%}.status{font-weight:700;margin:8px 0}.small{font-size:13px;color:var(--muted)}.hidden{display:none!important}.moves{max-height:140px;overflow:auto;font-size:14px;color:var(--muted)}.lessonText{line-height:1.5}.reviewtip{padding:10px;background:#263345;border-radius:10px;margin:8px 0}.pill{display:inline-block;border:1px solid #465467;border-radius:99px;padding:4px 8px;font-size:12px;color:var(--muted)}@media(min-width:650px){.gamegrid{display:grid;grid-template-columns:minmax(300px,1fr) minmax(220px,.7fr);gap:14px;align-items:start}.board{width:100%;max-width:520px}}
</style></head><body><main>
<h1>♟ checkMATE</h1><p>Your own chess engine · Play, learn, and review.</p>
<section class="panel"><div class="controls"><label>Play as <select id="color"><option value="white">White</option><option value="black">Black</option></select></label><label>Difficulty <select id="difficulty"><option value="easy">Easy</option><option value="normal" selected>Normal</option><option value="hard">Hard</option><option value="extreme">Extreme</option></select></label><button id="newGame" class="primary">New Game</button></div>
<div class="controls" style="margin-top:10px"><button id="playTab" class="active">Play Chess</button><button id="trainingTab">Training</button><button id="reviewTab">Game Review</button></div></section>
<div class="gamegrid"><section class="panel" id="gamePanel"><div id="status" class="status">Loading…</div><div id="board" class="board" aria-label="Interactive chessboard"></div><div class="small">Tap a piece, then tap its destination. Legal moves are highlighted.</div><div class="controls" style="margin-top:10px"><button id="flip">Flip Board</button><button id="resign">Resign</button></div></section>
<section class="panel"><div id="playContent"><h3>Game</h3><div class="small">Moves</div><div id="moves" class="moves">No moves yet.</div><p class="small">checkMATE calculates moves using its own search and evaluation code. Extreme may take longer on a phone.</p></div>
<div id="trainingContent" class="hidden"><h3>Training course</h3><p class="small">Training is optional. Pick a lesson, make a move on the board, and check your answer.</p><div class="controls"><select id="lessonSelect"><option value="0">1 · Control the centre</option><option value="1">2 · Develop a knight</option><option value="2">3 · Respond to ...e5</option></select><button id="loadLesson" class="primary">Start Lesson</button></div><p id="lessonPrompt" class="lessonText">Press Start Lesson to begin.</p><div id="lessonFeedback" class="reviewtip hidden"></div><button id="checkLesson">Check My Move</button><button id="lessonReset">Reset Lesson</button></div>
<div id="reviewContent" class="hidden"><h3>Game Review</h3><p id="reviewIntro">Finish a game to receive a quick review and recommendations.</p><div id="reviewTips"></div></div></section></div>
</main><script>
const glyph={'K':'♔','Q':'♕','R':'♖','B':'♗','N':'♘','P':'♙','k':'♚','q':'♛','r':'♜','b':'♝','n':'♞','p':'♟'};
let state=null,selected=null,legalTargets=[],flipped=false,lesson=null,lessonMove=null,lastUci=null,selectedTab='play';
async function api(url,body){const opts=body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{};const r=await fetch(url,opts);const d=await r.json();if(!r.ok)throw new Error(d.error||'Request failed');return d}
function parseFen(fen){const parts=fen.split(' '),rows=parts[0].split('/'),a=[];for(let r=0;r<8;r++){let row=[];for(const c of rows[r]){if(/[1-8]/.test(c)){for(let n=0;n<Number(c);n++)row.push('')}else row.push(c)}a.push(row)}return a}
function sqName(r,c){return 'abcdefgh'[c]+(8-r)}
function renderBoard(fen){const rows=parseFen(fen),el=document.getElementById('board');el.innerHTML='';let targets=new Set(legalTargets.map(m=>m.slice(2,4)));for(let rr=0;rr<8;rr++)for(let cc=0;cc<8;cc++){let r=flipped?7-rr:rr,c=flipped?7-cc:cc,p=rows[r][c],name=sqName(r,c),b=document.createElement('button');b.className='sq '+(((r+c)%2===0)?'light':'dark');if(p){b.textContent=glyph[p];b.classList.add(p===p.toUpperCase()?'whitepiece':'blackpiece')}if(selected===name)b.classList.add('selected');if(targets.has(name))b.classList.add(p?'capture':'target');b.setAttribute('aria-label',name+(p?' '+p:''));b.onclick=()=>tapSquare(name,p);el.appendChild(b)}}
async function refresh(){state=await api('/api/state');document.getElementById('status').textContent=state.status;document.getElementById('moves').textContent=state.moves.length?state.moves.map((m,i)=>(i%2===0?Math.floor(i/2)+1+'. ':'')+m.san).join(' '):'No moves yet.';renderBoard(state.fen);if(state.game_over){document.getElementById('reviewIntro').textContent='Result: '+state.result+'. Here are your quick recommendations:';showReview(await api('/api/review'))}}
function showReview(review){const box=document.getElementById('reviewTips');box.innerHTML='';if(!review||!review.tips){return}for(const tip of review.tips){const d=document.createElement('div');d.className='reviewtip';d.textContent=tip;box.appendChild(d)}if(review.result){document.getElementById('reviewIntro').textContent='Result: '+review.result+'. Quick review and recommendations:'}}
async function tapSquare(name,piece){if(!state||state.game_over||selectedTab==='training')return;if(selected){let target=legalTargets.find(m=>m.slice(2,4)===name);if(target){try{await api('/api/move',{uci:target});selected=null;legalTargets=[];await refresh()}catch(e){alert(e.message)}return}}if(piece){let color=piece===piece.toUpperCase()?'white':'black';if(color===state.human&&state.turn===color){selected=name;try{let d=await api('/api/legal/'+name);legalTargets=d.moves}catch(e){legalTargets=[]}renderBoard(state.fen);return}}selected=null;legalTargets=[];renderBoard(state.fen)}
async function startGame(){try{selected=null;legalTargets=[];document.getElementById('reviewTips').innerHTML='';await api('/api/new',{color:document.getElementById('color').value,difficulty:document.getElementById('difficulty').value});setTab('play');await refresh()}catch(e){alert(e.message)}}
function setTab(tab){selectedTab=tab;for(const t of ['play','training','review']){document.getElementById(t+'Tab').classList.toggle('active',t===tab);document.getElementById(t+'Content').classList.toggle('hidden',t!==tab);if(t==='play')document.getElementById('gamePanel').classList.toggle('hidden',false)}if(tab==='training'){loadLesson()}if(tab==='review'&&state?.game_over)api('/api/review').then(showReview)}
async function loadLesson(){try{lesson=await api('/api/lesson/'+document.getElementById('lessonSelect').value);lessonMove=null;document.getElementById('lessonPrompt').textContent=lesson.title+'. '+lesson.prompt;document.getElementById('lessonFeedback').classList.add('hidden');renderBoard(lesson.fen)}catch(e){alert(e.message)}}
document.getElementById('board').addEventListener('click',()=>{});
// Training board uses legal candidate moves from the backend; board squares still respond to taps.
async function trainingTap(name,piece){if(!lesson)return;const rows=parseFen(lesson.fen);if(!lessonMove){if(piece){lessonMove={from:name};document.getElementById('lessonFeedback').classList.remove('hidden');document.getElementById('lessonFeedback').textContent='Now tap the destination square.'}return}const uci=lessonMove.from+name;try{const result=await api('/api/training/check',{lesson:Number(document.getElementById('lessonSelect').value),uci});document.getElementById('lessonFeedback').classList.remove('hidden');document.getElementById('lessonFeedback').textContent=result.message;lastUci=uci;lessonMove=null}catch(e){document.getElementById('lessonFeedback').classList.remove('hidden');document.getElementById('lessonFeedback').textContent='Try a different move.';lessonMove=null}}
// Override board tap behavior for optional training lessons.
const oldRenderBoard=renderBoard;
renderBoard=function(fen){oldRenderBoard(fen);if(selectedTab==='training'){const el=document.getElementById('board');[...el.children].forEach((btn,i)=>{btn.onclick=()=>{const rr=Math.floor(i/8),cc=i%8,r=flipped?7-rr:rr,c=flipped?7-cc:cc;const rows=parseFen(lesson.fen);trainingTap(sqName(r,c),rows[r][c])}})}};
document.getElementById('newGame').onclick=startGame;document.getElementById('flip').onclick=()=>{flipped=!flipped;refresh()};document.getElementById('resign').onclick=async()=>{alert('Resignation is not yet supported in this prototype. You can start a new game.')} ;document.getElementById('playTab').onclick=()=>setTab('play');document.getElementById('trainingTab').onclick=()=>setTab('training');document.getElementById('reviewTab').onclick=()=>setTab('review');document.getElementById('loadLesson').onclick=loadLesson;document.getElementById('lessonReset').onclick=loadLesson;document.getElementById('checkLesson').onclick=()=>{document.getElementById('lessonFeedback').classList.remove('hidden');document.getElementById('lessonFeedback').textContent=lastUci?'Your move was checked above. Try another lesson or reset to practise again.':'Select a piece and destination on the board first.'};
refresh().catch(e=>document.getElementById('status').textContent='Start the app from Termux to play.');
</script></body></html>'''

@app.get("/api/legal/<square>")
def legal_moves(square):
    game = get_game(); b = game["board"]
    try: from_sq = chess.parse_square(square)
    except ValueError: return jsonify({"moves":[]})
    moves = [m.uci() for m in b.legal_moves if m.from_square == from_sq]
    return jsonify({"moves":moves})

@app.get("/api/review")
def review():
    game = get_game()
    if not game["board"].is_game_over(claim_draw=True): return jsonify({"result":None,"tips":["Finish a game to receive a review."]})
    return jsonify(make_review(game))

if __name__ == "__main__":
    print("checkMATE mobile chess app starting at http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
