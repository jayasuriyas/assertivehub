"""Public website on the main domain (assertivehub.in): home, about, products, features, pricing, contact, demo, FAQ, blog, login.
No JavaScript: menus, FAQs and the price switch are plain HTML/CSS, so the pages run under a strict Content-Security-Policy.
Text lives in site_content.py; plans and prices come from plans.py."""
import datetime
import hashlib
import html
import os
import re
import time

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.utils import timezone

from . import mail, plans, tenancy
from . import site_content as C
from .models import Lead, Post, SignupAttempt, Tenant

E_ = html.escape
YEAR = plans.YEAR_MONTHS_PAID


# ---------- icons (stroke SVG, 24x24) ----------
ICO = {
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'plane': '<path d="M10.5 13.5 3 11l1.5-1.5 7 1 4-4.5c1-1 2.6-1.3 3.3-.6s.4 2.3-.6 3.3l-4.5 4 1 7L13.5 21 11 13.5"/>',
    'check': '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    'calendar': '<rect x="3.5" y="5" width="17" height="15" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    'trophy': '<path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8.5 20h7M10 17h4"/>',
    'wallet': '<rect x="3" y="6" width="18" height="13" rx="3"/><path d="M16 12.5h2M3 9.5h18M6 6l9-3 1 3"/>',
    'building': '<path d="M4 21V6l8-3v18M12 9h8v12M7 9h2M7 13h2M7 17h2M15 13h2M15 17h2M2 21h20"/>',
    'briefcase': '<rect x="3" y="7" width="18" height="13" rx="3"/><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18"/>',
    'shield': '<path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5V6z"/><path d="m9 12 2 2 4-4"/>',
    'map': '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.4"/>',
    'phone': '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
    'toggle': '<rect x="2.5" y="7" width="19" height="10" rx="5"/><circle cx="16.5" cy="12" r="3"/>',
    'arrow': '<path d="M5 12h14M13 6l6 6-6 6"/>',
    'menu': '<path d="M4 7h16M4 12h16M4 17h16"/>',
    'mail': '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7 8 6 8-6"/>',
    'chat': '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 10h8M8 13h5"/>',
    'tick': '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    'dash': '<path d="M7 12h10"/>',
    'finger': '<path d="M7 11a5 5 0 0 1 10 0v2a14 14 0 0 1-1.5 6.5M12 11v3a10 10 0 0 1-2 6M4.5 15a17 17 0 0 0 .5-4 7 7 0 0 1 11.6-5.2M19.5 11v1a19 19 0 0 1-.6 4.5M9.3 18.5A12 12 0 0 0 9.5 15v-4a2.5 2.5 0 0 1 5 0"/>',
    'spark': '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
    'users': '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14a5.5 5.5 0 0 1 3.5 6"/>',
    'lock': '<rect x="4.5" y="10" width="15" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    'star': '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
}


def ic(name, cls='i'):
    return '<svg class="%s" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">%s</svg>' % (cls, ICO.get(name, ICO['spark']))


# ---------- styles ----------
CSS = """
:root{--ink:#0e0f1a;--mu:#5d6177;--bg:#fbfaff;--card:#fff;--line:#ece9f6;--ac:#5b3df5;--ac2:#8b5cf6;--pk:#ec4899;--or:#fb923c;--good:#16a34a;
--sh:0 24px 60px -28px rgba(70,50,170,.35);--sh2:0 10px 30px -18px rgba(40,30,110,.35);--r:20px;
--f:"Inter","Manrope","SF Pro Display",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
*{box-sizing:border-box}html{scroll-behavior:smooth}
main>section,.ft{position:relative}
body{margin:0;font:16px/1.6 var(--f);color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:var(--ac);text-decoration:none}a:hover{text-decoration:underline}
img,svg{max-width:100%}
.i{width:20px;height:20px;flex:none}
.w{max-width:1180px;margin:0 auto;padding:0 24px}
.skip{position:absolute;left:-999px}.skip:focus{left:12px;top:12px;background:#fff;padding:8px 12px;border-radius:8px;z-index:99}
:focus-visible{outline:3px solid rgba(91,61,245,.45);outline-offset:2px;border-radius:8px}
/* header */
.hd{position:sticky;top:0;z-index:40;background:rgba(251,250,255,.82);backdrop-filter:saturate(1.4) blur(14px);-webkit-backdrop-filter:saturate(1.4) blur(14px);border-bottom:1px solid transparent}
.hd .w{display:flex;align-items:center;gap:20px;height:74px}
.logo{display:flex;align-items:center;gap:9px;font-weight:800;font-size:19px;color:var(--ink);letter-spacing:-.01em}.logo:hover{text-decoration:none}
.logo i{width:26px;height:26px;border-radius:50%;background:conic-gradient(from 200deg,var(--ac),var(--pk),var(--or),var(--ac));display:inline-flex;align-items:center;justify-content:center}
.logo i:after{content:"";width:10px;height:10px;border-radius:50%;background:#fff}
.nav{margin:0 auto;display:flex;gap:2px;background:#fff;border:1px solid var(--line);border-radius:999px;padding:5px;box-shadow:var(--sh2)}
.nav a{color:var(--ink);font-size:14px;font-weight:500;padding:7px 14px;border-radius:999px}.nav a:hover{text-decoration:none;background:#f3f1fc}
.nav a.on{color:var(--ac);font-weight:600}
.hr{display:flex;align-items:center;gap:14px}.hr .lk{color:var(--ink);font-weight:500;font-size:14px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;background:var(--ink);color:#fff;border:0;border-radius:999px;padding:12px 22px;font:600 15px/1 var(--f);cursor:pointer;white-space:nowrap;transition:transform .15s,box-shadow .15s}
.btn:hover{text-decoration:none;transform:translateY(-1px);box-shadow:0 12px 24px -12px rgba(14,15,26,.6)}
.btn.sm{padding:9px 16px;font-size:14px}.btn.ac{background:linear-gradient(135deg,var(--ac),var(--ac2))}
.btn.gh{background:#fff;color:var(--ink);border:1px solid var(--line)}.btn.gh:hover{box-shadow:var(--sh2)}
.mnav{display:none;position:relative}.mnav summary{list-style:none;cursor:pointer;display:flex;padding:8px;border-radius:10px;border:1px solid var(--line);background:#fff}
.mnav summary::-webkit-details-marker{display:none}
.mnav div{position:absolute;right:0;top:48px;width:min(86vw,320px);background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:var(--sh);padding:10px;display:flex;flex-direction:column}
.mnav div a{color:var(--ink);padding:11px 12px;border-radius:10px;font-weight:500}.mnav div a:hover{background:#f3f1fc;text-decoration:none}
@media(max-width:980px){.nav,.hr .lk,.hr .btn{display:none}.mnav{display:block}.hd .w{justify-content:space-between}}
/* glow backgrounds */
.glow{position:relative;isolation:isolate}
.glow:before{content:"";position:absolute;inset:-10% -5% auto auto;width:640px;height:640px;z-index:-1;pointer-events:none;
background:radial-gradient(closest-side,rgba(236,72,153,.22),transparent 70%),radial-gradient(closest-side at 30% 60%,rgba(139,92,246,.28),transparent 70%),radial-gradient(closest-side at 70% 70%,rgba(251,146,60,.18),transparent 70%);filter:blur(10px)}
.glow.l:before{inset:auto auto -20% -10%}
/* type */
.ey{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--mu);margin-bottom:14px}
h1,h2,h3{letter-spacing:-.02em;line-height:1.15;margin:0 0 14px}
h1{font-size:clamp(38px,5.4vw,62px);font-weight:800}
h2{font-size:clamp(28px,3.6vw,42px);font-weight:750}
h3{font-size:19px;font-weight:700}
.gt{background:linear-gradient(100deg,var(--ac) 10%,var(--ac2) 50%,var(--pk));-webkit-background-clip:text;background-clip:text;color:transparent}
.lead{font-size:18px;color:var(--mu);max-width:620px}
.mu{color:var(--mu)}.c{text-align:center}.c .lead{margin-left:auto;margin-right:auto}
.sec{padding:96px 0}.sec.tight{padding:64px 0}.alt{background:linear-gradient(180deg,#f6f3ff,#fbfaff)}
.hd2{max-width:720px;margin-bottom:44px}.hd2.c{margin-left:auto;margin-right:auto}
.row{display:flex;justify-content:space-between;align-items:flex-end;gap:30px;flex-wrap:wrap;margin-bottom:44px}.row .lead{margin:0;max-width:440px;font-size:16px}
/* hero */
.hero{padding:70px 0 90px}.hero .w{display:grid;grid-template-columns:1.05fr 1fr;gap:50px;align-items:center}
.hero .lead{margin:6px 0 28px}
.cap{display:flex;gap:8px;max-width:520px;background:#fff;border:1px solid var(--line);border-radius:999px;padding:6px;box-shadow:var(--sh2)}
.cap input{flex:1;min-width:0;border:0;background:transparent;font:15px var(--f);padding:0 14px;color:var(--ink)}.cap input:focus{outline:none}
.note{font-size:13px;color:var(--mu);margin-top:12px;display:flex;gap:16px;flex-wrap:wrap}.note span{display:inline-flex;align-items:center;gap:6px}.note .i{width:16px;height:16px;color:var(--good)}
.hero .w>*,.split>*{min-width:0}
@media(max-width:980px){.hero .w{grid-template-columns:minmax(0,1fr)}.hero{padding:40px 0 60px}}
@media(max-width:480px){.cap{flex-direction:column;border-radius:20px}.cap input{padding:12px 14px}.cap .btn{width:100%}.w{padding:0 18px}.ey{letter-spacing:.08em}}
/* hero visual: floating cards */
.vis{position:relative;min-height:500px}
.vis .blob{position:absolute;inset:8% 4% 6% 10%;border-radius:40px;background:radial-gradient(circle at 30% 30%,rgba(139,92,246,.35),transparent 60%),radial-gradient(circle at 75% 60%,rgba(236,72,153,.28),transparent 55%),radial-gradient(circle at 50% 90%,rgba(251,146,60,.22),transparent 55%);filter:blur(24px)}
.fc{position:absolute;background:var(--card);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh);padding:18px}
.fc.p1{left:0;top:0;width:49%;max-width:270px;text-align:center}
.av{width:62px;height:62px;border-radius:50%;margin:0 auto 10px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:20px;background:linear-gradient(135deg,var(--ac),var(--pk));box-shadow:0 0 0 5px #f3efff}
.fc.p1 b{display:block}.fc.p1 small{color:var(--mu);font-size:12px}
.st3{display:flex;justify-content:space-around;margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}.st3 div{font-size:11px;color:var(--mu)}.st3 b{font-size:18px;color:var(--ink)}
.fc.p2{left:2%;top:62%;width:46%;max-width:250px;background:linear-gradient(135deg,var(--ac),#7c4dff);color:#fff;border:0}
.fc.p2 small{opacity:.8;font-size:12px}.fc.p2 b{font-size:24px;display:block;font-variant-numeric:tabular-nums}
.fc.p2 svg.sp{position:absolute;right:14px;bottom:16px;width:80px;height:34px}
.fc.p3{right:0;top:27%;width:48%;max-width:270px}
.fc.p3 .fp{width:44px;height:44px;border-radius:14px;background:#f3efff;color:var(--ac);display:flex;align-items:center;justify-content:center;margin-bottom:12px}.fc.p3 .fp .i{width:28px;height:28px}
.fc.p3 b{display:block;font-size:16px}.fc.p3 p{font-size:13px;color:var(--mu);margin:6px 0 14px}
.fc.p3 .btn{width:100%;background:linear-gradient(135deg,var(--ac),var(--ac2))}
.fc.p4{right:0;top:82%;width:52%;max-width:280px;display:flex;gap:12px;align-items:center;padding:14px 16px}
.fc.p4 .dt{width:44px;text-align:center;background:#fff4ec;color:#c2410c;border-radius:12px;padding:5px 0;font-weight:800;line-height:1.1}.fc.p4 .dt small{display:block;font-size:10px;font-weight:600}
.fc.p4 b{font-size:14px;display:block}.fc.p4 small{font-size:12px;color:var(--mu)}
.live{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:var(--good)}.live:before{content:"";width:8px;height:8px;border-radius:50%;background:var(--good);box-shadow:0 0 0 4px rgba(22,163,74,.18)}
@media(max-width:560px){.vis{min-height:480px}.fc{padding:14px}.fc.p3{display:none}.fc.p1{width:64%;max-width:none}.fc.p2{left:auto;right:0;top:54%;width:58%}.fc.p4{right:auto;left:4%;top:80%;width:84%;max-width:none}}
@media(prefers-reduced-motion:no-preference){.fc{animation:fl 7s ease-in-out infinite}.fc.p2{animation-delay:-2s}.fc.p3{animation-delay:-4s}.fc.p4{animation-delay:-5.5s}
@keyframes fl{50%{transform:translateY(-8px)}}}
/* strip */
.strip{padding:56px 0;text-align:center}.strip h2{font-size:clamp(24px,3vw,32px)}
.chips{display:flex;flex-wrap:wrap;gap:12px;justify-content:center;margin-top:22px}
.chip{display:inline-flex;align-items:center;gap:8px;padding:10px 18px;border:1px solid var(--line);background:#fff;border-radius:999px;font-weight:600;font-size:14px;color:#34364a}
.chip .i{width:16px;height:16px;color:var(--ac)}
/* cards */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:20px}
.g3{grid-template-columns:repeat(3,1fr)}.g4{grid-template-columns:repeat(4,1fr)}.g2{grid-template-columns:repeat(2,1fr)}
@media(max-width:980px){.g3,.g4{grid-template-columns:repeat(2,1fr)}}@media(max-width:620px){.g3,.g4,.g2{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:28px;box-shadow:0 1px 0 rgba(0,0,0,.02);transition:box-shadow .2s,transform .2s;display:flex;flex-direction:column}
a.card{color:inherit}a.card:hover{text-decoration:none;box-shadow:var(--sh);transform:translateY(-3px)}
.card p{color:var(--mu);font-size:15px;margin:0 0 18px;flex:1}
.ib{width:44px;height:44px;border-radius:14px;background:#f3efff;color:var(--ac);display:flex;align-items:center;justify-content:center;margin-bottom:18px}
.more{font-weight:600;font-size:14px;color:var(--ink);display:inline-flex;align-items:center;gap:6px}.more .i{width:16px;height:16px}
.card.dark{background:var(--ink);color:#fff;border-color:var(--ink)}.card.dark p{color:#b9bbd0}.card.dark .ib{background:rgba(255,255,255,.1);color:#fff}.card.dark .more{color:#fff}
/* split */
.split{display:grid;grid-template-columns:1fr 1fr;gap:64px;align-items:center}@media(max-width:980px){.split{grid-template-columns:1fr;gap:40px}}
.ticks{list-style:none;padding:0;margin:22px 0 28px;display:grid;gap:12px}.ticks li{display:flex;gap:10px;align-items:flex-start}
.ticks .i{width:20px;height:20px;color:#fff;background:var(--good);border-radius:50%;padding:3px;flex:none;margin-top:2px}
.ticks.inl{display:flex;flex-wrap:wrap;gap:10px 22px;font-size:14px;font-weight:500}.ticks.inl .i{width:18px;height:18px}
.mock{position:relative;padding:20px}.mock .card{box-shadow:var(--sh)}
.tbl{width:100%;border-collapse:collapse;font-size:14px}.tbl td,.tbl th{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left}.tbl th{font-size:12px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.tbl tr:last-child td{border-bottom:0}.tbl .n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.tbl td:first-child{white-space:nowrap}
.bar{height:6px;border-radius:6px;background:#efecfb;overflow:hidden;min-width:60px}.bar i{display:block;height:100%;border-radius:6px;background:linear-gradient(90deg,var(--ac),var(--pk))}
/* stats */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;background:#fff;border:1px solid var(--line);border-radius:24px;padding:30px;box-shadow:var(--sh2);margin-top:70px;text-align:center}
.stats b{display:block;font-size:clamp(28px,3vw,38px);font-weight:800;letter-spacing:-.02em}.stats span{color:var(--mu);font-size:14px}
@media(max-width:760px){.stats{grid-template-columns:repeat(2,1fr)}}
/* hub (integrations) */
.hub{position:relative;max-width:760px;margin:0 auto;height:360px}
.hub .core{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);background:#fff;border:1px solid var(--line);border-radius:999px;padding:18px 34px;font-weight:800;font-size:18px;box-shadow:var(--sh);z-index:2}
.hub svg.ln{position:absolute;inset:0;width:100%;height:100%;z-index:0}
.hub .nd{position:absolute;transform:translate(-50%,-50%);display:flex;flex-direction:column;align-items:center;gap:8px;z-index:1;font-size:13px;font-weight:600;color:#34364a}
.hub .nd span .i{width:24px;height:24px}.hub .nd span{width:58px;height:58px;border-radius:50%;background:#fff;border:1px solid var(--line);box-shadow:var(--sh2);display:flex;align-items:center;justify-content:center;color:var(--ac);font-weight:800}
@media(max-width:620px){.hub{height:auto;display:flex;flex-wrap:wrap;justify-content:center;gap:18px}.hub .core,.hub .nd{position:static;transform:none}.hub .core{width:100%;text-align:center}.hub svg.ln{display:none}}
/* faq */
.faq{max-width:820px;margin:0 auto;display:grid;gap:12px}
.faq details{background:#fff;border:1px solid var(--line);border-radius:16px;padding:0 22px;transition:box-shadow .2s}.faq details[open]{box-shadow:var(--sh2)}
.faq summary{cursor:pointer;list-style:none;padding:18px 0;font-weight:600;display:flex;justify-content:space-between;gap:16px;align-items:center}.faq summary::-webkit-details-marker{display:none}
.faq summary:after{content:"+";font-size:22px;color:var(--ac);font-weight:400;line-height:1}.faq details[open] summary:after{content:"−"}
.faq details p{margin:0 0 18px;color:var(--mu)}
/* testimonials */
.tq p{font-style:italic;color:#34364a}.stars{color:#f59e0b;display:flex;gap:2px;margin:0 0 14px}.stars .i{width:16px;height:16px;fill:currentColor}
.who{display:flex;align-items:center;gap:10px;font-size:14px}.who .av{width:38px;height:38px;font-size:14px;margin:0;box-shadow:none}.who small{display:block;color:var(--mu);font-size:12px}
/* blog */
.post .th{height:170px;border-radius:14px;margin:-12px -12px 18px;background:linear-gradient(135deg,#ede9fe,#fce7f3 60%,#ffedd5);display:flex;align-items:flex-end;padding:14px}
.post .th span{font-size:12px;font-weight:700;background:#fff;color:var(--ac);border-radius:999px;padding:4px 12px}
.post h3{font-size:18px}.meta{font-size:13px;color:var(--mu);margin-bottom:10px}
.article{max-width:760px;margin:0 auto}.article h2{font-size:28px;margin-top:38px}.article h3{margin-top:28px}.article p,.article li{font-size:17px;color:#2b2d3d}.article li{margin:6px 0}
/* cta */
.cta{border-radius:32px;padding:64px 40px;text-align:center;color:#fff;background:radial-gradient(circle at 15% 20%,rgba(236,72,153,.55),transparent 45%),radial-gradient(circle at 85% 80%,rgba(251,146,60,.45),transparent 45%),linear-gradient(135deg,#3b23c9,#6d3df5)}
.cta h2{color:#fff}.cta p{color:rgba(255,255,255,.82);max-width:560px;margin:0 auto 26px;font-size:18px}.cta .btn{background:#fff;color:var(--ink)}.cta .btn.gh{background:transparent;color:#fff;border-color:rgba(255,255,255,.5)}
.btns{display:flex;gap:12px;flex-wrap:wrap}.c .btns,.cta .btns{justify-content:center}
/* pricing */
#pm,#py{position:absolute;opacity:0;width:1px;height:1px;pointer-events:none}.sw{display:flex;justify-content:center;margin-bottom:40px}
.sw .tg{display:inline-flex;background:#fff;border:1px solid var(--line);border-radius:999px;padding:5px;box-shadow:var(--sh2)}
.sw label{padding:9px 18px;border-radius:999px;font-weight:600;font-size:14px;cursor:pointer;color:var(--mu);white-space:nowrap}
#pm:checked~.sw label[for=pm],#py:checked~.sw label[for=py]{background:var(--ink);color:#fff}
#pm:focus-visible~.sw label[for=pm],#py:focus-visible~.sw label[for=py]{outline:3px solid rgba(91,61,245,.45)}
.save{font-size:11px;background:#dcfce7;color:#166534;border-radius:999px;padding:2px 8px;margin-left:6px}
.yr{display:none}#py:checked~.plans .yr{display:block}#py:checked~.plans .mo{display:none}
.plans{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;align-items:stretch}@media(max-width:980px){.plans{grid-template-columns:1fr;max-width:460px;margin:0 auto}}
.plan{position:relative}.plan.pop{border:2px solid var(--ac);box-shadow:var(--sh)}
.plan .tag{position:absolute;top:-13px;left:28px;background:linear-gradient(135deg,var(--ac),var(--pk));color:#fff;font-size:12px;font-weight:700;border-radius:999px;padding:4px 12px}
.price{font-size:44px;font-weight:800;letter-spacing:-.03em;line-height:1;margin:14px 0 6px}.price small{font-size:15px;font-weight:500;color:var(--mu);letter-spacing:0}
.plan ul{list-style:none;padding:0;margin:20px 0 26px;display:grid;gap:10px;flex:1;align-content:start}.plan li{display:flex;gap:10px;font-size:14px}.plan li .i{width:18px;height:18px;color:var(--good);flex:none;margin-top:2px}
.plan .btn{width:100%}
.mx{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border:1px solid var(--line);border-radius:20px;overflow:hidden;font-size:15px}
.mx th,.mx td{padding:13px 18px;border-bottom:1px solid var(--line);text-align:center}.mx th:first-child,.mx td:first-child{text-align:left}
.mx thead th{background:#f7f5ff;font-size:14px}.mx tr.gh td{background:#fbfaff;font-weight:700;font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--mu)}
.mx .i{width:20px;height:20px}.mx .y{color:var(--good)}.mx .x{color:#c7c9d6}.mx tr:last-child td{border-bottom:0}
.scroll{overflow-x:auto}
/* forms */
.form{background:#fff;border:1px solid var(--line);border-radius:24px;padding:32px;box-shadow:var(--sh2)}
.fg{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}@media(max-width:620px){.fg{grid-template-columns:1fr}}
.form label,.fl{display:block;font-weight:600;font-size:14px;margin:16px 0 6px}
.form input,.form select,.form textarea,.in{width:100%;padding:12px 14px;border-radius:12px;border:1px solid #dcd8ec;background:#fff;font:15px var(--f);color:var(--ink)}
.form select,select.in{appearance:none;-webkit-appearance:none;padding-right:40px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%235d6177' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E") no-repeat right 14px center/18px}
.form textarea{min-height:130px;resize:vertical}.form input:focus,.form select:focus,.form textarea:focus,.in:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 4px rgba(91,61,245,.12)}
.form .btn{margin-top:22px}.hp{position:absolute;left:-5000px;width:1px;height:1px;overflow:hidden}
.ok{background:#ecfdf3;border:1px solid #bbf7d0;color:#14532d;border-radius:14px;padding:16px 18px;margin-bottom:18px}
.err{background:#fef2f2;border:1px solid #fecaca;color:#991b1b;border-radius:14px;padding:12px 16px;margin:12px 0}
.sub{display:flex;align-items:center;border:1px solid #dcd8ec;border-radius:12px;background:#fff;overflow:hidden}.sub:focus-within{border-color:var(--ac);box-shadow:0 0 0 4px rgba(91,61,245,.12)}
.sub input{border:0;box-shadow:none!important;border-radius:0}.sub span{padding:0 14px;color:var(--mu);font-size:14px;white-space:nowrap}
.info{display:grid;gap:16px}.info .card{flex-direction:row;gap:14px;align-items:flex-start;padding:20px}.info .card p{margin:0}
/* page hero */
.ph{padding:70px 0 40px;text-align:center}.ph .lead{margin:0 auto}
.crumb{font-size:13px;color:var(--mu);margin-bottom:14px}.crumb a{color:var(--mu)}
.pill{display:inline-block;font-size:12px;font-weight:700;color:var(--ac);background:#f3efff;border-radius:999px;padding:4px 12px;margin:2px}
/* footer */
.ft{border-top:1px solid var(--line);padding:64px 0 30px;margin-top:40px;background:#fff}
.ft .cols{display:grid;grid-template-columns:1.6fr repeat(3,1fr);gap:30px}@media(max-width:760px){.ft .cols{grid-template-columns:1fr 1fr}}
.ft h4{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--mu);margin:0 0 14px}.ft ul{list-style:none;padding:0;margin:0;display:grid;gap:9px}.ft ul a{color:var(--ink);font-size:14px}
.ft .bt{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;border-top:1px solid var(--line);margin-top:40px;padding-top:20px;font-size:13px;color:var(--mu)}
/* legacy pages (sign-up, owner panel, legal) */
.lg .c2,.lg .c{background:#fff;border:1px solid var(--line);border-radius:18px;padding:22px;text-align:left}
.lg .b{display:inline-block;background:var(--ink);color:#fff;text-decoration:none;padding:11px 20px;border-radius:999px;font-weight:600;border:0;cursor:pointer;font-size:15px}
.lg .b.o{background:#fff;color:var(--ink);border:1px solid var(--line)}
.lg .grid{grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}.lg .pr{font-size:28px;font-weight:800}.lg .tag{font-size:12px;background:var(--ac);color:#fff;border-radius:99px;padding:2px 10px;margin-left:6px}
.lg label{display:block;font-weight:600;margin:14px 0 4px;font-size:14px}.lg input,.lg select,.lg textarea{width:100%;padding:11px 12px;border-radius:12px;border:1px solid #dcd8ec;background:#fff;color:var(--ink);font:15px var(--f)}
.lg select{appearance:none;-webkit-appearance:none;padding-right:40px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%235d6177' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E") no-repeat right 14px center/18px}
.lg .err{background:#fef2f2;color:#991b1b;padding:10px 12px;border-radius:12px;margin:10px 0}.lg ul.t{padding-left:18px}.lg .hero{padding:20px 0;display:block}
.lg nav.on a{margin-right:14px}
@media print{.hd,.ft,.cta{display:none}}
"""
CSS_TAG = hashlib.sha256(CSS.encode()).hexdigest()[:10]


def css(request):
    r = HttpResponse(CSS, content_type='text/css; charset=utf-8')
    r['Cache-Control'] = 'public, max-age=31536000, immutable'
    return r


def favicon(request):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           '<stop offset="0" stop-color="#5b3df5"/><stop offset=".6" stop-color="#ec4899"/><stop offset="1" stop-color="#fb923c"/></linearGradient></defs>'
           '<circle cx="16" cy="16" r="15" fill="url(#g)"/><circle cx="16" cy="16" r="6" fill="#fff"/></svg>')
    r = HttpResponse(svg, content_type='image/svg+xml')
    r['Cache-Control'] = 'public, max-age=86400'
    return r


# ---------- layout ----------
NAV = [('/', 'Home'), ('/products', 'Products'), ('/features', 'Features'), ('/pricing', 'Pricing'), ('/about', 'About'), ('/blog', 'Blog'), ('/contact', 'Contact')]


def _origin(request):
    return '%s://%s' % ('https' if (request.is_secure() or settings.CSRF_COOKIE_SECURE) else 'http', request.get_host())


def csp(request):
    port = request.get_host().partition(':')[2]
    sub = '*.%s%s' % (settings.BASE_DOMAIN, ':' + port if port else '')
    return ("default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self' https://%s http://%s; "
            "base-uri 'none'; frame-ancestors 'none'" % (sub, sub))


def respond(request, h, status=200):
    r = HttpResponse(h, status=status)
    r['Content-Security-Policy'] = csp(request)
    r['X-Content-Type-Options'] = 'nosniff'
    r['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return r


def layout(request, title, body, desc='', active='', wrap=True, noindex=False):
    P = E_(plans.PRODUCT)
    path = request.path
    full = '%s | %s' % (title, plans.PRODUCT) if title and title != plans.PRODUCT else '%s - HR and work management software for Indian teams' % plans.PRODUCT
    desc = desc or 'Attendance, leave, tasks, time tracking, meetings, performance and payroll in one simple online workspace. Every company gets its own private web address. Free %d-day trial.' % plans.TRIAL_DAYS
    canon = _origin(request) + path
    nav = ''.join('<a href="%s"%s>%s</a>' % (u, ' class="on" aria-current="page"' if active == u else '', t) for u, t in NAV)
    mnav = ''.join('<a href="%s">%s</a>' % (u, t) for u, t in NAV) + '<a href="/demo">Book a demo</a><a href="/login">Log in</a><a href="/signup">Start free trial</a>'
    head = ('<!doctype html><html lang="en-IN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><meta name="description" content="%s"><link rel="canonical" href="%s">%s'
            '<meta property="og:type" content="website"><meta property="og:title" content="%s"><meta property="og:description" content="%s"><meta property="og:url" content="%s">'
            '<meta name="theme-color" content="#5b3df5"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/site.css?v=%s"></head>') % (
        E_(full), E_(desc), E_(canon), '<meta name="robots" content="noindex">' if noindex else '', E_(full), E_(desc), E_(canon), CSS_TAG)
    hd = ('<a class="skip" href="#main">Skip to content</a><header class="hd"><div class="w"><a class="logo" href="/" aria-label="%s home"><i></i>%s</a>'
          '<nav class="nav" aria-label="Main">%s</nav><div class="hr"><a class="lk" href="/login">Log in</a><a class="btn sm" href="/signup">Start free trial</a>'
          '<details class="mnav"><summary aria-label="Menu">%s</summary><div>%s</div></details></div></div></header>') % (P, P, nav, ic('menu'), mnav)
    areas = ''.join('<li><a href="/products/%s">%s</a></li>' % (a['key'], E_(a['title'])) for a in C.AREAS[:5])
    ft = ('<footer class="ft"><div class="w"><div class="cols"><div><a class="logo" href="/"><i></i>%s</a><p class="mu" style="max-width:320px;font-size:14px">HR and work management for growing teams in India. '
          'Every company gets its own private workspace at <b>yourcompany.%s</b>.</p><a class="btn sm" href="/demo">Book a demo</a></div>'
          '<div><h4>Product</h4><ul><li><a href="/products">Overview</a></li><li><a href="/features">Features</a></li><li><a href="/pricing">Pricing</a></li>%s</ul></div>'
          '<div><h4>Company</h4><ul><li><a href="/about">About us</a></li><li><a href="/blog">Blog</a></li><li><a href="/contact">Contact</a></li><li><a href="/faq">FAQs</a></li></ul></div>'
          '<div><h4>Get started</h4><ul><li><a href="/signup">Start free trial</a></li><li><a href="/login">Log in</a></li><li><a href="/demo">Book a demo</a></li><li><a href="/terms">Terms</a></li><li><a href="/privacy">Privacy</a></li></ul></div></div>'
          '<div class="bt"><span>© %d %s. Made in India. Prices in INR, GST extra.</span><span><a href="/terms">Terms</a> · <a href="/privacy">Privacy</a> · <a href="/sitemap.xml">Sitemap</a></span></div></div></footer>') % (
        P, E_(settings.BASE_DOMAIN), areas, datetime.date.today().year, P)
    main = '<main id="main">%s</main>' % (('<div class="w lg" style="padding:40px 24px 60px">%s</div>' % body) if wrap else body)
    return head + '<body>' + hd + main + ft + '</body></html>'


def render(request, title, body, **kw):
    status = kw.pop('status', 200)
    return respond(request, layout(request, title, body, **kw), status)


def cta(title='Ready to bring your team together?', text=None):
    text = text or 'Start your %d-day free trial with every feature switched on. No card needed.' % plans.TRIAL_DAYS
    return ('<section class="sec tight"><div class="w"><div class="cta"><h2>%s</h2><p>%s</p><div class="btns"><a class="btn" href="/signup">Start free trial</a>'
            '<a class="btn gh" href="/demo">Book a demo</a></div></div></div></section>') % (E_(title), E_(text))


def _fmt(s):
    return s.replace('{trial}', str(plans.TRIAL_DAYS)).replace('{domain}', settings.BASE_DOMAIN)


def faq_html(items):
    return '<div class="faq">%s</div>' % ''.join('<details><summary>%s</summary><p>%s</p></details>' % (E_(_fmt(q)), E_(_fmt(a))) for q, a in items)


def area_cards(dark_first=True):
    out = ''
    for i, a in enumerate(C.AREAS):
        out += '<a class="card%s" href="/products/%s"><div class="ib">%s</div><h3>%s</h3><p>%s</p><span class="more">Read more %s</span></a>' % (
            ' dark' if dark_first and i == 0 else '', a['key'], ic(a['icon']), E_(a['title']), E_(a['short']), ic('arrow'))
    return out


def _posts(n=None):
    q = Post.objects.filter(published=True).order_by('-published_at')
    return list(q[:n] if n else q)


def post_cards(posts):
    return ''.join('<a class="card post" href="/blog/%s"><div class="th"><span>%s</span></div><div class="meta">%s · %s</div><h3>%s</h3><p>%s</p><span class="more">Read article %s</span></a>' % (
        p.slug, E_(p.tag or 'Article'), E_(p.author or plans.PRODUCT), p.published_at.strftime('%d %b %Y') if p.published_at else '', E_(p.title), E_(p.excerpt), ic('arrow')) for p in posts)


def testimonials():
    if not C.TESTIMONIALS:
        return ''
    cards = ''.join('<div class="card tq"><div class="stars">%s</div><p>"%s"</p><div class="who"><span class="av">%s</span><span><b>%s</b><small>%s, %s</small></span></div></div>' % (
        ic('star') * 5, E_(t['quote']), E_(''.join(w[0] for w in t['name'].split()[:2]).upper()), E_(t['name']), E_(t.get('role', '')), E_(t.get('company', ''))) for t in C.TESTIMONIALS)
    return '<section class="sec"><div class="w"><div class="hd2 c"><span class="ey">Testimonials</span><h2>What our customers say</h2></div><div class="grid g3">%s</div></div></section>' % cards


def _min_price():
    return min(p['month'] for p in plans.PLANS.values())


def _all_modules():
    seen = []
    for p in plans.PLANS.values():
        for m in p['modules']:
            if m not in seen:
                seen.append(m)
    return seen


# ---------- pages ----------
def home(request):
    T = plans.TRIAL_DAYS
    sparks = '<svg class="sp" viewBox="0 0 80 34" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"><path d="M2 26c6 0 7-14 13-14s6 16 12 16 7-22 13-22 6 14 12 14 7-8 13-8 6 6 13 6"/></svg>'
    hero = ('<section class="hero glow"><div class="w"><div><span class="ey">HR and work management for Indian teams</span>'
            '<h1>Run your whole team from <span class="gt">one</span> simple workspace.</h1>'
            '<p class="lead">Attendance, leave, tasks, time tracking, meetings, performance and payroll in one place. Every company gets its own private web address.</p>'
            '<form class="cap" action="/signup" method="get" role="search" aria-label="Start a free trial"><label class="hp" for="hc">Company name</label><input id="hc" name="company" placeholder="Your company name" maxlength="80" autocomplete="organization">'
            '<button class="btn" type="submit">Start free trial</button></form>'
            '<div class="note"><span>%s %d-day free trial</span><span>%s No card needed</span><span>%s Data kept private</span></div></div>'
            '<div class="vis" aria-hidden="true"><div class="blob"></div>'
            '<div class="fc p1"><div class="av">AK</div><b>Anita Kumar</b><small>Design lead · Bengaluru</small><div class="st3"><div><b>22</b><br>Present</div><div><b>1</b><br>Leave</div><div><b>14</b><br>Tasks done</div></div></div>'
            '<div class="fc p2"><small>Working on · Brand refresh</small><b>01:24:10</b><small>Estimate 2h 00m</small>%s</div>'
            '<div class="fc p3"><div class="fp">%s</div><b>Check in with a tap</b><p>Location, selfie or QR code. The register fills itself.</p><span class="btn sm">Check in</span></div>'
            '<div class="fc p4"><div class="dt">11<small>NOV</small></div><div><b>Client review</b><small>11:00 · Google Meet</small><br><span class="live">Starting soon</span></div></div>'
            '</div></div></section>') % (ic('tick'), T, ic('tick'), ic('tick'), sparks, ic('finger'))
    strip = '<section class="strip"><div class="w"><h2>Built for teams that move fast</h2><p class="mu">From five-person studios to companies with several branches.</p><div class="chips">%s</div></div></section>' % (
        ''.join('<span class="chip">%s%s</span>' % (ic('spark'), E_(x)) for x in C.FOR_WHOM))
    services = ('<section class="sec alt glow l"><div class="w"><div class="row"><div><span class="ey">What you get</span><h2>Everything your team runs on,<br>in one place</h2></div>'
                '<p class="lead">Switch on only the parts you need. Each one works on its own and together with the rest.</p></div><div class="grid g4">%s</div></div></section>') % area_cards()
    rows = [('Head Office', 'HO', 42, 45, 2), ('Bengaluru', 'BLR', 27, 30, 1), ('Chennai', 'MAA', 16, 18, 0)]
    mock = ''.join('<tr><td><b>%s</b> <span class="mu">%s</span></td><td class="n">%d/%d</td><td style="width:40%%"><div class="bar"><i style="width:%d%%"></i></div></td><td class="n">%d late</td></tr>' % (n, c, p, h, round(p / h * 100), l) for n, c, p, h, l in rows)
    split = ('<section class="sec"><div class="w split"><div class="mock"><div class="card"><div class="meta">Example dashboard</div><h3>Branches today</h3><table class="tbl"><thead><tr><th>Branch</th><th class="n">Present</th><th></th><th class="n">Late</th></tr></thead><tbody>%s</tbody></table></div></div>'
             '<div><span class="ey">Every branch, one view</span><h2>See what is happening across your company, right now</h2><p class="lead">Head office sees every branch. Branch admins see only their own. Each branch keeps its own working hours, holidays and reports.</p>'
             '<ul class="ticks inl"><li>%s Role-based access</li><li>%s Branch transfers with history</li><li>%s WhatsApp and SMS alerts</li></ul><a class="btn" href="/products/branches">Explore branches</a></div></div>'
             '<div class="w"><div class="stats"><div><b>%d days</b><span>free trial, every feature</span></div><div><b>₹%s</b><span>per month, starting price</span></div><div><b>%d+</b><span>modules you can switch on</span></div><div><b>1</b><span>private database per company</span></div></div></div></section>') % (
        mock, ic('tick'), ic('tick'), ic('tick'), T, format(_min_price(), ','), len(_all_modules()))
    pos = [(50, 8), (88, 28), (88, 75), (50, 93), (12, 75), (12, 28)]
    lines = ''.join('<line x1="50%%" y1="50%%" x2="%d%%" y2="%d%%" stroke="#d9d3f5" stroke-width="1.5" stroke-dasharray="5 6"/>' % (x, y) for x, y in pos)
    nodes = ''.join('<div class="nd" style="left:%d%%;top:%d%%"><span>%s</span>%s</div>' % (x, y, ic(icn), E_(n)) for (x, y), (n, icn) in zip(pos, C.CONNECTS))
    hub = ('<section class="sec alt"><div class="w"><div class="hd2 c"><span class="ey">Works with what you use</span><h2>Connected to the tools your team already has</h2>'
           '<p class="lead">Alerts on WhatsApp and SMS, meetings in Google Calendar and Outlook, payments through Razorpay and punches from biometric machines.</p></div>'
           '<div class="hub"><svg class="ln" aria-hidden="true">%s</svg><div class="core">%s</div>%s</div></div></section>') % (lines, E_(plans.PRODUCT), nodes)
    why = '<section class="sec"><div class="w"><div class="hd2 c"><span class="ey">Why teams choose us</span><h2>Simple for your team. Serious about your data.</h2></div><div class="grid g4">%s</div></div></section>' % ''.join(
        '<div class="card"><div class="ib">%s</div><h3>%s</h3><p>%s</p></div>' % (ic(i), E_(t), E_(d)) for i, t, d in C.WHY)
    faq = ('<section class="sec alt"><div class="w split" style="align-items:start"><div><span class="ey">FAQs</span><h2>Frequently asked questions</h2><p class="lead">Everything you need to know before you start. Can\'t find an answer?</p>'
           '<div class="btns"><a class="btn" href="/faq">See all FAQs</a><a class="btn gh" href="/contact">Ask us</a></div></div>%s</div></section>') % faq_html(C.FAQS[:5])
    posts = _posts(3)
    blog = ('<section class="sec"><div class="w"><div class="row"><div><span class="ey">From the blog</span><h2>Tips, guides and updates</h2></div><a class="btn gh" href="/blog">All articles</a></div><div class="grid g3">%s</div></div></section>' % post_cards(posts)) if posts else ''
    body = hero + strip + services + split + hub + why + testimonials() + faq + blog + cta()
    return render(request, plans.PRODUCT, body, wrap=False, active='/')


def about(request):
    body = ('<section class="ph glow"><div class="w"><span class="ey">About us</span><h1>Helping teams <span class="gt">work better</span>, together</h1>'
            '<p class="lead">%s started with a simple idea: the tools a growing company uses to run its people should be as easy as the apps on your phone, and priced for Indian businesses.</p></div></section>'
            '<section class="sec tight"><div class="w split"><div><span class="ey">Our story</span><h2>Built from real problems</h2>'
            '<p class="lead" style="font-size:17px">Attendance on paper, leave on WhatsApp, tasks in spreadsheets and reviews from memory. We built one workspace that brings it all together, so managers spend less time chasing and more time leading.</p>'
            '<p class="mu">Today it covers attendance, leave, tasks and time tracking, meetings, performance, payroll and multi-branch management, and every company gets its own private workspace and web address.</p></div>'
            '<div class="grid g2">%s</div></div></section>'
            '<section class="sec alt"><div class="w"><div class="hd2 c"><span class="ey">What we believe</span><h2>Our principles</h2></div><div class="grid g4">%s</div></div></section>') % (
        E_(plans.PRODUCT),
        ''.join('<div class="card"><div class="ib">%s</div><h3>%s</h3><p>%s</p></div>' % (ic(i), t, d) for i, t, d in [
            ('users', 'People first', 'Fair rules, clear approvals and honest records for everyone.'), ('shield', 'Privacy', 'Each company has its own separate database.'),
            ('spark', 'Simplicity', 'Only switch on what you need. Everything else stays out of the way.'), ('map', 'Made for India', 'Rupee pricing, GST invoices and Indian payroll rules.')]),
        ''.join('<div class="card"><div class="ib">%s</div><h3>%s</h3><p>%s</p></div>' % (ic(i), E_(t), E_(d)) for i, t, d in C.WHY))
    return render(request, 'About us', body + cta(), wrap=False, active='/about', desc='Who we are and why we built %s, the HR and work-management workspace for Indian teams.' % plans.PRODUCT)


def products(request):
    steps = [('1', 'Create your workspace', 'Choose your company web address, like yourcompany.%s. It takes a minute.' % settings.BASE_DOMAIN),
             ('2', 'Set up your company', 'Add branches, working hours, holidays and people, or import them. Switch on the modules you need.'),
             ('3', 'Invite your team', 'Everyone signs in from their phone or computer. Admins, managers and employees each see what they should.')]
    body = ('<section class="ph glow"><div class="w"><span class="ey">Our product</span><h1>%s</h1><p class="lead">One workspace for attendance, leave, tasks, time, meetings, performance and payroll. Pick the parts you need today and add more as you grow.</p>'
            '<div class="btns" style="justify-content:center;margin-top:26px"><a class="btn" href="/signup">Start free trial</a><a class="btn gh" href="/features">Compare features</a></div></div></section>'
            '<section class="sec tight"><div class="w"><div class="grid g4">%s</div></div></section>'
            '<section class="sec alt"><div class="w"><div class="hd2 c"><span class="ey">How it works</span><h2>Up and running the same day</h2></div><div class="grid g3">%s</div></div></section>') % (
        E_(plans.PRODUCT), area_cards(),
        ''.join('<div class="card"><div class="ib" style="font-weight:800">%s</div><h3>%s</h3><p>%s</p></div>' % (n, E_(t), E_(d)) for n, t, d in steps))
    return render(request, 'Products', body + cta(), wrap=False, active='/products', desc='%s: attendance, leave, tasks, time tracking, meetings, performance, payroll and branches in one workspace.' % plans.PRODUCT)


def _plans_with(module):
    return [p['name'] for p in plans.PLANS.values() if module in p['modules']]


def product(request, key):
    a = next((x for x in C.AREAS if x['key'] == key), None)
    if not a:
        raise Http404
    pts = ''.join('<div class="card"><div class="ib">%s</div><h3>%s</h3><p>%s</p></div>' % (ic('tick'), E_(t), E_(d)) for t, d in a['points'])
    inc = ''.join('<tr><td>%s</td>%s</tr>' % (E_(C.MODULE_LABEL.get(m, m)), ''.join('<td>%s</td>' % (ic('tick', 'i y') if m in p['modules'] else ic('dash', 'i x')) for p in plans.PLANS.values()))
                  for m in a['modules'])
    thead = '<tr><th>Included in</th>%s</tr>' % ''.join('<th>%s</th>' % E_(p['name']) for p in plans.PLANS.values())
    others = ''.join('<a class="chip" href="/products/%s">%s%s</a>' % (x['key'], ic(x['icon']), E_(x['title'])) for x in C.AREAS if x['key'] != key)
    body = ('<section class="ph glow"><div class="w"><div class="crumb"><a href="/products">Products</a> / %s</div><div class="ib" style="margin:0 auto 18px;width:56px;height:56px">%s</div>'
            '<h1>%s</h1><p class="lead">%s</p><div class="btns" style="justify-content:center;margin-top:26px"><a class="btn" href="/signup">Try it free</a><a class="btn gh" href="/demo">Book a demo</a></div></div></section>'
            '<section class="sec tight"><div class="w"><div class="grid g3">%s</div></div></section>'
            '<section class="sec tight alt"><div class="w" style="max-width:860px"><div class="hd2 c"><h2>Which plans include it</h2><p class="lead">All of it is switched on during your free trial.</p></div>'
            '<div class="scroll"><table class="mx"><thead>%s</thead><tbody>%s</tbody></table></div><p class="c" style="margin-top:18px"><a href="/pricing">See prices</a> · <a href="/features">Full feature list</a></p></div></section>'
            '<section class="sec tight"><div class="w c"><h3>Explore more</h3><div class="chips">%s</div></div></section>') % (
        E_(a['title']), ic(a['icon']), E_(a['title']), E_(a['intro']), pts, thead, inc, others)
    return render(request, a['title'], body + cta(), wrap=False, active='/products', desc=a['short'])


EVERY_PLAN = ['Your own web address (yourcompany.%s)', 'Separate private database', 'Super admin, admin, manager and employee roles', 'Works on phone and desktop',
              'Audit log of every change', 'CSV downloads', 'Module on/off switches', 'Email support']


def features(request):
    names = [p['name'] for p in plans.PLANS.values()]
    rows = ''
    shown = set()
    for a in C.AREAS:
        ms = [m for m in a['modules'] if m not in shown and m in _all_modules()]
        if not ms:
            continue
        rows += '<tr class="gh"><td colspan="%d">%s</td></tr>' % (len(names) + 1, E_(a['title']))
        for m in ms:
            shown.add(m)
            rows += '<tr><td>%s</td>%s</tr>' % (E_(C.MODULE_LABEL.get(m, m)), ''.join('<td>%s</td>' % (ic('tick', 'i y') if m in p['modules'] else ic('dash', 'i x')) for p in plans.PLANS.values()))
    rest = [m for m in _all_modules() if m not in shown]
    if rest:
        rows += '<tr class="gh"><td colspan="%d">More</td></tr>' % (len(names) + 1)
        rows += ''.join('<tr><td>%s</td>%s</tr>' % (E_(C.MODULE_LABEL.get(m, m)), ''.join('<td>%s</td>' % (ic('tick', 'i y') if m in p['modules'] else ic('dash', 'i x')) for p in plans.PLANS.values())) for m in rest)
    lim = '<tr class="gh"><td colspan="%d">Limits</td></tr><tr><td>People</td>%s</tr><tr><td>Branches</td>%s</tr>' % (
        len(names) + 1, ''.join('<td>Up to %d</td>' % p['emp'] for p in plans.PLANS.values()), ''.join('<td>%s</td>' % (p.get('branches') or 'Unlimited') for p in plans.PLANS.values()))
    every = ''.join('<li>%s %s</li>' % (ic('tick'), E_(x % settings.BASE_DOMAIN if '%s' in x else x)) for x in EVERY_PLAN)
    body = ('<section class="ph glow"><div class="w"><span class="ey">Features</span><h1>Every feature, <span class="gt">plan by plan</span></h1><p class="lead">Start with what you need. Your free trial includes everything, so you can try it all before you choose.</p></div></section>'
            '<section class="sec tight"><div class="w" style="max-width:980px"><div class="card" style="margin-bottom:28px"><h3>Included in every plan</h3><ul class="ticks inl" style="margin:10px 0 0">%s</ul></div>'
            '<div class="scroll"><table class="mx"><thead><tr><th>Feature</th>%s</tr></thead><tbody>%s%s</tbody></table></div>'
            '<p class="c" style="margin-top:24px"><a class="btn" href="/pricing">See pricing</a></p></div></section>') % (
        every, ''.join('<th>%s</th>' % E_(n) for n in names), rows, lim)
    return render(request, 'Features', body + cta(), wrap=False, active='/features', desc='Compare every %s feature across plans: attendance, leave, tasks, time tracking, meetings, payroll, branches and more.' % plans.PRODUCT)


def pricing(request):
    cards = ''
    for k, p in plans.PLANS.items():
        yearly = p['month'] * YEAR
        feats = ['Up to %d people' % p['emp'], '%s branch%s' % (p.get('branches') or 'Unlimited', '' if p.get('branches') == 1 else 'es'), p['blurb']]
        feats += [C.MODULE_LABEL[m] for m in p['modules'] if m in ('timetrack', 'meetings', 'notify', 'branches', 'payroll', 'biometric', 'checkin')][:4]
        cards += ('<div class="card plan%s">%s<h3>%s</h3><div class="mo"><div class="price">₹%s<small> /month</small></div><div class="mu" style="font-size:13px">+ 18%% GST, billed monthly</div></div>'
                  '<div class="yr"><div class="price">₹%s<small> /year</small></div><div class="mu" style="font-size:13px">+ 18%% GST · that is ₹%s a month</div></div>'
                  '<ul>%s</ul><a class="btn%s" href="/signup?plan=%s">Start free trial</a></div>') % (
            ' pop' if k == 'business' else '', '<span class="tag">Most popular</span>' if k == 'business' else '', E_(p['name']), format(p['month'], ','), format(yearly, ','),
            format(round(yearly / 12), ','), ''.join('<li>%s %s</li>' % (ic('tick'), E_(f)) for f in feats), ' ac' if k == 'business' else '', k)
    bill = [x for x in C.FAQS if any(w in x[0] for w in ('trial', 'payments', 'change or cancel'))]
    body = ('<section class="ph glow"><div class="w"><span class="ey">Pricing</span><h1>Simple prices in <span class="gt">rupees</span></h1><p class="lead">Every plan starts with a %d-day free trial of all features. No card needed. Pay monthly, or yearly and get two months free.</p></div></section>'
            '<section class="sec tight" style="padding-top:20px"><div class="w"><input type="radio" name="per" id="pm" checked><input type="radio" name="per" id="py">'
            '<div class="sw"><div class="tg"><label for="pm">Monthly</label><label for="py">Yearly <span class="save">2 months free</span></label></div></div><div class="plans">%s</div>'
            '<p class="c mu" style="margin-top:26px;font-size:14px">Prices exclude GST at 18%%. A GST invoice is created for every payment. Need more than %d people? <a href="/contact">Talk to us</a>.</p>'
            '<p class="c"><a href="/features">Compare every feature %s</a></p></div></section>'
            '<section class="sec tight alt"><div class="w"><div class="hd2 c"><h2>Billing questions</h2></div>%s</div></section>') % (
        plans.TRIAL_DAYS, cards, max(p['emp'] for p in plans.PLANS.values()), ic('arrow', 'i'), faq_html(bill))
    return render(request, 'Pricing', body + cta(), wrap=False, active='/pricing', desc='%s plans from ₹%s a month. Free %d-day trial, GST invoices, pay monthly or yearly.' % (plans.PRODUCT, format(_min_price(), ','), plans.TRIAL_DAYS))


def faq(request):
    body = ('<section class="ph glow"><div class="w"><span class="ey">FAQs</span><h1>Frequently asked questions</h1><p class="lead">Can\'t find your answer? <a href="/contact">Send us a message</a> and we will get back to you.</p></div></section>'
            '<section class="sec tight"><div class="w">%s</div></section>') % faq_html(C.FAQS)
    return render(request, 'FAQs', body + cta(), wrap=False, desc='Answers about the %s free trial, web address, data privacy, branches, payments and alerts.' % plans.PRODUCT)


# ---------- blog ----------
def _inline(s):
    s = E_(s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
    return re.sub(r'\[([^\]]+)\]\((https?://[^\s)&]+|/[^\s)&]*)\)', r'<a href="\2" rel="noopener">\1</a>', s)


def body_html(text):
    out = []
    for blk in re.split(r'\n\s*\n', (text or '').strip()):
        lines = [x.rstrip() for x in blk.strip().split('\n') if x.strip()]
        if not lines:
            continue
        if lines[0].startswith('### '):
            out.append('<h3>%s</h3>' % _inline(lines[0][4:]))
            lines = lines[1:]
        elif lines[0].startswith('## '):
            out.append('<h2>%s</h2>' % _inline(lines[0][3:]))
            lines = lines[1:]
        if not lines:
            continue
        if all(x.lstrip().startswith('- ') for x in lines):
            out.append('<ul>%s</ul>' % ''.join('<li>%s</li>' % _inline(x.lstrip()[2:]) for x in lines))
        else:
            out.append('<p>%s</p>' % '<br>'.join(_inline(x) for x in lines))
    return ''.join(out)


def blog(request):
    posts = _posts()
    grid = '<div class="grid g3">%s</div>' % post_cards(posts) if posts else '<div class="card c" style="max-width:560px;margin:0 auto"><h3>Articles are on their way</h3><p>Guides on attendance, leave, time tracking and running a growing team will appear here soon.</p></div>'
    body = ('<section class="ph glow"><div class="w"><span class="ey">Blog</span><h1>Tips, guides and <span class="gt">updates</span></h1><p class="lead">Practical ideas for running a happier, more organised team.</p></div></section>'
            '<section class="sec tight"><div class="w">%s</div></section>') % grid
    return render(request, 'Blog', body + cta(), wrap=False, active='/blog', desc='Guides and product updates from %s.' % plans.PRODUCT)


def blog_post(request, slug):
    p = Post.objects.filter(slug=slug, published=True).first()
    if not p:
        raise Http404
    more = [x for x in _posts(4) if x.pk != p.pk][:3]
    body = ('<section class="ph glow"><div class="w article"><div class="crumb"><a href="/blog">Blog</a> / %s</div><h1 style="font-size:clamp(32px,4.4vw,48px)">%s</h1><div class="meta">%s · %s</div></div></section>'
            '<section class="sec tight" style="padding-top:10px"><div class="w"><article class="article">%s</article></div></section>%s') % (
        E_(p.tag or 'Article'), E_(p.title), E_(p.author or plans.PRODUCT), p.published_at.strftime('%d %B %Y') if p.published_at else '', body_html(p.body),
        ('<section class="sec tight alt"><div class="w"><h2 class="c">More articles</h2><div class="grid g3">%s</div></div></section>' % post_cards(more)) if more else '')
    return render(request, p.title, body + cta(), wrap=False, active='/blog', desc=p.excerpt)


# ---------- forms: contact and demo ----------
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _ip(request):
    from core.views import client_ip
    return client_ip(request)


def _limited(request, kind, n=5):
    key = ('%s|%s' % (kind, _ip(request)))[:45]
    SignupAttempt.objects.filter(ip__startswith=kind + '|', at__lt=time.time() - 3600).delete()
    if SignupAttempt.objects.filter(ip=key).count() >= n:
        return True
    SignupAttempt.objects.create(ip=key, at=time.time())
    return False


def _notify(lead):
    to = os.environ.get('HUB_OWNER_EMAIL') or os.environ.get('HUB_CONTACT_EMAIL')
    if not to:
        return
    lines = ['%s: %s' % (k, v) for k, v in [('Name', lead.name), ('Email', lead.email), ('Phone', lead.phone), ('Company', lead.company), ('Team size', lead.team_size),
                                              ('Topic', lead.topic), ('Preferred time', lead.preferred)] if v]
    mail.send(to, 'New %s from the website: %s' % ('demo request' if lead.kind == 'demo' else 'message', lead.name), '\n'.join(lines) + '\n\n' + lead.message + '\n\nSee all: /owner/leads')


def _field(name, label, v, typ='text', req=True, extra=''):
    return '<label for="f_%s">%s%s</label><input id="f_%s" name="%s" type="%s" value="%s"%s%s>' % (
        name, E_(label), '' if req else ' <span class="mu" style="font-weight:400">(optional)</span>', name, name, typ, E_(v.get(name, '')), ' required' if req else '', extra)


def _select(name, label, opts, v, req=True):
    return '<label for="f_%s">%s</label><select id="f_%s" name="%s"%s><option value="">Choose…</option>%s</select>' % (
        name, E_(label), name, name, ' required' if req else '', ''.join('<option%s>%s</option>' % (' selected' if o == v.get(name) else '', E_(o)) for o in opts))


def _lead_form(request, kind):
    v, err = {}, ''
    if request.method == 'POST':
        v = {k: request.POST.get(k, '').strip() for k in ('name', 'email', 'phone', 'company', 'team_size', 'topic', 'preferred', 'message')}
        if request.POST.get('website'):  # honeypot: bots fill every field
            return HttpResponseRedirect(request.path + '?sent=1')
        if not 2 <= len(v['name']) <= 80:
            err = 'Please enter your name.'
        elif not EMAIL_RE.match(v['email']) or len(v['email']) > 120:
            err = 'Please enter a valid email address.'
        elif v['phone'] and not re.match(r'^[+0-9 ()-]{7,20}$', v['phone']):
            err = 'Please enter a valid phone number.'
        elif kind == 'demo' and not v['company']:
            err = 'Please enter your company name.'
        elif kind == 'demo' and v['team_size'] not in C.TEAM_SIZES:
            err = 'Please choose your team size.'
        elif kind == 'contact' and not 10 <= len(v['message']) <= 3000:
            err = 'Please write a message (at least 10 characters).'
        elif len(v['message']) > 3000:
            err = 'Please keep the message under 3000 characters.'
        elif _limited(request, kind):
            err = 'Too many messages from your network. Please try again in an hour, or email us.'
        if not err:
            lead = Lead.objects.create(kind=kind, name=v['name'], email=v['email'], phone=v['phone'][:20], company=v['company'][:120],
                                       team_size=v['team_size'] if kind == 'demo' else '', topic=v['topic'] if v['topic'] in C.CONTACT_TOPICS else '',
                                       preferred=v['preferred'][:40], message=v['message'], ip=_ip(request))
            _notify(lead)
            return HttpResponseRedirect(request.path + '?sent=1')
    sent = request.GET.get('sent') == '1'
    tok = get_token(request)
    if kind == 'demo':
        fields = ('<div class="fg"><div>%s</div><div>%s</div><div>%s</div><div>%s</div><div>%s</div><div>%s</div></div>' % (
            _field('name', 'Your name', v, extra=' autocomplete="name" maxlength="80"'), _field('email', 'Work email', v, 'email', extra=' autocomplete="email"'),
            _field('phone', 'Mobile number', v, 'tel', False, ' autocomplete="tel"'), _field('company', 'Company', v, extra=' autocomplete="organization" maxlength="120"'),
            _select('team_size', 'Team size', C.TEAM_SIZES, v), _field('preferred', 'Preferred day and time', v, req=False, extra=' maxlength="40" placeholder="e.g. Tuesday afternoon"')) +
            '<label for="f_message">What would you like to see? <span class="mu" style="font-weight:400">(optional)</span></label><textarea id="f_message" name="message" maxlength="3000">%s</textarea>' % E_(v.get('message', '')))
        btn = 'Request my demo'
    else:
        fields = ('<div class="fg"><div>%s</div><div>%s</div><div>%s</div><div>%s</div></div>' % (
            _field('name', 'Your name', v, extra=' autocomplete="name" maxlength="80"'), _field('email', 'Email', v, 'email', extra=' autocomplete="email"'),
            _field('phone', 'Phone', v, 'tel', False, ' autocomplete="tel"'), _select('topic', 'Topic', C.CONTACT_TOPICS, v, False)) +
            _field('company', 'Company', v, req=False, extra=' autocomplete="organization" maxlength="120"') +
            '<label for="f_message">Message</label><textarea id="f_message" name="message" required minlength="10" maxlength="3000">%s</textarea>' % E_(v.get('message', '')))
        btn = 'Send message'
    done = ('<div class="ok"><b>Thank you!</b> %s</div>' % ('We have your request and will contact you within one working day to fix a time.' if kind == 'demo' else 'Your message has reached us. We usually reply within one working day.')) if sent else ''
    form = '<form class="form" method="post" novalidate>%s%s<input type="hidden" name="csrfmiddlewaretoken" value="%s"><div class="hp" aria-hidden="true"><label for="f_web">Leave empty</label><input id="f_web" name="website" tabindex="-1" autocomplete="off"></div>%s<button class="btn ac" type="submit">%s %s</button></form>' % (
        done, '<div class="err" role="alert">%s</div>' % E_(err) if err else '', tok, fields, btn, ic('arrow'))
    return form, err


def _contact_info():
    items = []
    if os.environ.get('HUB_CONTACT_EMAIL'):
        e = os.environ['HUB_CONTACT_EMAIL']
        items.append(('mail', 'Email', '<a href="mailto:%s">%s</a>' % (E_(e), E_(e))))
    if os.environ.get('HUB_CONTACT_PHONE'):
        ph = os.environ['HUB_CONTACT_PHONE']
        items.append(('phone', 'Phone', '<a href="tel:%s">%s</a>' % (E_(re.sub(r'[^+0-9]', '', ph)), E_(ph))))
    if os.environ.get('HUB_SELLER_ADDRESS'):
        items.append(('map', 'Office', E_(os.environ['HUB_SELLER_ADDRESS'])))
    items.append(('chat', 'Support', 'Signed-in customers can also reach us from inside their workspace.'))
    return ''.join('<div class="card"><div class="ib" style="margin:0">%s</div><div><b>%s</b><p>%s</p></div></div>' % (ic(i), t, d) for i, t, d in items)


def contact(request):
    res = _lead_form(request, 'contact')
    if isinstance(res, HttpResponse):
        return res
    form, err = res
    body = ('<section class="ph glow"><div class="w"><span class="ey">Contact us</span><h1>We would love to <span class="gt">hear from you</span></h1><p class="lead">Questions about plans, features or getting started? Send us a message.</p></div></section>'
            '<section class="sec tight" style="padding-top:10px"><div class="w split" style="align-items:start"><div class="info">%s<div class="card"><div class="ib" style="margin:0">%s</div><div><b>Prefer a walkthrough?</b><p><a href="/demo">Book a free demo</a> and we will show you around.</p></div></div></div>%s</div></section>') % (
        _contact_info(), ic('calendar'), form)
    return render(request, 'Contact us', body, wrap=False, active='/contact', status=400 if err else 200, desc='Contact the %s team about plans, features, billing or support.' % plans.PRODUCT)


def demo(request):
    res = _lead_form(request, 'demo')
    if isinstance(res, HttpResponse):
        return res
    form, err = res
    pts = ['A 30-minute video call at a time that suits you', 'Set-up for your branches, hours and leave rules', 'Answers to your questions about plans and data', 'No obligation, no card needed']
    body = ('<section class="ph glow"><div class="w"><span class="ey">Book a demo</span><h1>See %s <span class="gt">in action</span></h1><p class="lead">Tell us a little about your team and we will set up a personal walkthrough.</p></div></section>'
            '<section class="sec tight" style="padding-top:10px"><div class="w split" style="align-items:start"><div><h2>What you get</h2><ul class="ticks">%s</ul><p class="mu">Rather try it yourself? <a href="/signup">Start a free trial</a> in a minute.</p></div>%s</div></section>') % (
        E_(plans.PRODUCT), ''.join('<li>%s %s</li>' % (ic('tick'), E_(x)) for x in pts), form)
    return render(request, 'Book a demo', body, wrap=False, status=400 if err else 200, desc='Book a free personal demo of %s.' % plans.PRODUCT)


# ---------- login: find your workspace ----------
def login(request):
    from .pviews import _base_url
    err = ''
    s = request.GET.get('company', '').strip().lower()
    if s:
        s = re.sub(r'^https?://', '', s).split('.')[0].split('/')[0]
        t = Tenant.objects.filter(slug=s).first() if tenancy.valid_slug(s) else None
        if t:
            return HttpResponseRedirect(_base_url(request, t.slug) + '/')
        err = 'We could not find a workspace called "%s". Check the spelling, or find it by email below.' % s
    found = request.GET.get('found') == '1'
    body = ('<section class="ph glow"><div class="w" style="max-width:560px"><span class="ey">Log in</span><h1>Sign in to your <span class="gt">workspace</span></h1><p class="lead">Every company has its own address. Enter yours and we will take you there.</p></div></section>'
            '<section class="sec tight" style="padding-top:0"><div class="w" style="max-width:560px"><form class="form" method="get" action="/login">%s<label for="f_co">Your workspace address</label>'
            '<div class="sub"><input id="f_co" name="company" class="in" required pattern="[A-Za-z0-9-]{3,30}" maxlength="30" autocapitalize="none" autocomplete="off" placeholder="yourcompany" value="%s"><span>.%s</span></div>'
            '<button class="btn" type="submit" style="width:100%%">Continue %s</button></form>'
            '<details class="faq" style="margin-top:20px"><summary>Forgot your workspace address?</summary><form method="post" action="/login/find" style="padding-bottom:18px">%s<input type="hidden" name="csrfmiddlewaretoken" value="%s">'
            '<label class="fl" for="f_em">The email you signed up with</label><input id="f_em" class="in" type="email" name="email" required autocomplete="email"><p><button class="btn sm" type="submit">Email me my workspace</button></p></form></details>'
            '<p class="c mu" style="margin-top:26px">New here? <a href="/signup">Create a workspace</a>, free for %d days.</p></div></section>') % (
        '<div class="err" role="alert">%s</div>' % E_(err) if err else '', E_(s), E_(settings.BASE_DOMAIN), ic('arrow'),
        '<div class="ok">If that email has a workspace, we have sent its address. Check your inbox.</div>' if found else '', get_token(request), plans.TRIAL_DAYS)
    return render(request, 'Log in', body, wrap=False, noindex=True, status=404 if err else 200, desc='Sign in to your %s workspace.' % plans.PRODUCT)


def login_find(request):
    from .pviews import _base_url
    if request.method != 'POST':
        return HttpResponseRedirect('/login')
    em = request.POST.get('email', '').strip().lower()
    if EMAIL_RE.match(em) and not _limited(request, 'find', 5):
        ts = [t for t in Tenant.objects.all() if t.owner_email.strip().lower() == em]
        if ts:
            mail.send(em, 'Your %s workspace address' % plans.PRODUCT, 'Hello,\n\nHere %s your workspace address%s:\n\n%s\n\nIf you did not ask for this, you can ignore this email.' % (
                'is' if len(ts) == 1 else 'are', '' if len(ts) == 1 else 'es', '\n'.join('%s: %s' % (t.name, _base_url(request, t.slug)) for t in ts)))
    return HttpResponseRedirect('/login?found=1')  # same answer either way, so nobody can test which emails have accounts


# ---------- SEO ----------
def sitemap(request):
    o = _origin(request)
    urls = ['/', '/products', '/features', '/pricing', '/about', '/contact', '/demo', '/faq', '/blog', '/signup', '/terms', '/privacy']
    urls += ['/products/%s' % a['key'] for a in C.AREAS]
    items = ''.join('<url><loc>%s%s</loc></url>' % (E_(o), u) for u in urls)
    items += ''.join('<url><loc>%s/blog/%s</loc><lastmod>%s</lastmod></url>' % (E_(o), p.slug, timezone.localtime(p.updated).date().isoformat()) for p in _posts())
    return HttpResponse('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>' % items, content_type='application/xml')


def robots(request):
    return HttpResponse('User-agent: *\nDisallow: /owner/\nDisallow: /login\nAllow: /\nSitemap: %s/sitemap.xml\n' % _origin(request), content_type='text/plain')


def not_found(request, exception=None):
    body = ('<section class="ph glow"><div class="w"><span class="ey">404</span><h1>We could not find that page</h1><p class="lead">It may have moved. Try one of these instead.</p>'
            '<div class="btns" style="justify-content:center;margin-top:24px"><a class="btn" href="/">Home</a><a class="btn gh" href="/products">Products</a><a class="btn gh" href="/contact">Contact</a></div></div></section>')
    return render(request, 'Page not found', body, wrap=False, noindex=True, status=404)
