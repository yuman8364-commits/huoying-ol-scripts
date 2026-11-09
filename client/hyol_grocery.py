"""Selected moon-seal goods only, with durable quotes and stock/balance reconciliation."""
import re
import time
import uuid
import json
from functools import lru_cache
import hyol_scheduler as hs
from hyol_errors import TaskUnavailable

ITEMS = {
    'practice_potion': ('修行药水', 200), 'secret_pages': ('秘卷残页', 300),
    'myoboku_harvest': ('妙木山的收获', 200), 'myoboku_training': ('妙木山的训练', 200),
    'myoboku_orb': ('妙木山的灵球', 200), 'cultivation_ticket': ('修炼券', 200),
    'mantra_elixir': ('真言合剂', 400),
}
TITLE = 'grocery_title_current.bmp'
BODY = (500, 260, 1345, 970)


def validate_items(items):
    if (not isinstance(items, (list, tuple)) or any(type(x) is not str or x not in ITEMS for x in items)
            or len(set(items)) != len(items)):
        raise ValueError('杂货屋商品选择无效')
    return tuple(code for code in ITEMS if code in items)


def text(image):
    from hyol_ocr import recognize
    data = recognize(image)
    return re.sub(r'\s+', '', data.get('text') or ''.join(row.get('text', '') for row in data.get('lines', [])))


def number(image, *, threshold=190):
    from PIL import Image, ImageOps
    import numpy as np
    rgb=np.asarray(image.convert('RGB'),dtype=np.int16)
    mask=(rgb.min(axis=2)>threshold)&((rgb.max(axis=2)-rgb.min(axis=2))<35)
    rows=np.flatnonzero(mask.any(axis=1));cols=np.flatnonzero(mask.any(axis=0))
    if not len(rows) or not len(cols):return None
    glyph=mask[rows[0]:rows[-1]+1,cols[0]:cols[-1]+1]
    groups=np.split(cols,np.where(np.diff(cols)>1)[0]+1)
    if not 1<=len(groups)<=9 or any(len(group)>12 for group in groups) or glyph.shape[0]>18:
        return None
    materials=number_glyphs()
    digits=[]
    for group in groups:
        part=mask[:,group[0]:group[-1]+1];ys=np.flatnonzero(part.any(axis=1))
        key=tuple(''.join('1' if b else '0' for b in row) for row in part[ys[0]:ys[-1]+1])
        matches=materials.get(key,set())
        if len(matches)!=1:break
        digits.append(next(iter(matches)))
    if len(digits)==len(groups) and 1<=len(digits)<=9:return int(''.join(digits))
    reference=hs.ASSETS/'grocery_quantity_one.bmp'
    if reference.is_file():
        ref=np.asarray(Image.open(reference).convert('L'))>127
        if ref.shape==glyph.shape and np.array_equal(ref,glyph):return 1
    clean=ImageOps.expand(Image.fromarray(np.where(mask,0,255).astype('uint8')),12,fill=255)
    clean=clean.resize((clean.width*2,clean.height*2))
    value = text(clean)
    return int(value) if re.fullmatch(r'\d{1,9}', value) and len(value)==len(groups) else None


@lru_cache(maxsize=1)
def number_glyphs():
    path=hs.ASSETS/'grocery_number_glyphs.json'
    result={}
    if path.is_file():
        for row in json.loads(path.read_text('utf-8'))['variants']:
            result.setdefault(tuple(row['rows']),set()).add(row['digit'])
    return result


def stock_number(value):
    match = re.fullmatch(r'库存[:：]?(\d{1,4})', re.sub(r'\s+', '', value))
    return int(match[1]) if match else None


def purchase_quantity(stock, balance, price):
    if any(type(v) is not int for v in (stock, balance, price)) or not 0 <= stock <= 999 or balance < 0 or price <= 0:
        raise hs.PendingError('商品库存、价格或月之印余额未可靠识别')
    return stock if stock * price <= balance else 0


def reconciled(quote, offer, balance):
    return (offer is not None and offer['price'] == quote['price']
            and offer['stock'] == quote['stock'] - quote['quantity']
            and balance == quote['balance'] - quote['quantity'] * quote['price'])


class Grocery:
    def __init__(self, runner):
        self.r, self.s, self.account = runner, runner.scheduler, runner.account
        self.selected = validate_items(getattr(self.s, 'grocery_items', ()))
        self.rec, self.ledger = runner.record('grocery'), None
        self.deadline=time.monotonic()+600

    def check(self):
        self.s.check_stop()
        if time.monotonic()>getattr(self,'deadline',float('inf')):
            raise hs.PendingError('杂货屋本次处理已到10分钟上限，保留当前报价与原操作')

    def save(self):
        if self.ledger is not None:
            self.ledger.save()
        else:
            self.r.save()

    def ready(self, frame):
        return self.s.template(TITLE, (800, 80, 1110, 190), frame)[0]

    def click_asset(self, name, roi, label, *, irreversible=False, predicate=None):
        self.check()
        from hyol_identity_overlays import clear_hover_preview
        clear_hover_preview(self.s)
        frame = self.s.capture()
        if name in ('grocery_scroll_up.bmp','grocery_scroll_down.bmp'):
            import numpy as np
            box=(1352,255,1375,284) if name.endswith('_up.bmp') else (1352,934,1375,958)
            with hs.Image.open(hs.ASSETS/name) as im:reference=np.asarray(im.convert('RGB'),dtype=np.int16)
            def white(rgb):return (rgb.min(axis=2)>180)&((rgb.max(axis=2)-rgb.min(axis=2))<35)
            mask=white(reference)
            def verified(current):
                if not self.ready(current):return False
                actual=white(np.asarray(current.crop(box).convert('RGB'),dtype=np.int16))
                return int(mask.sum())>=10 and np.array_equal(mask,actual)
            if not verified(frame):raise hs.PendingError('杂货屋滚动箭头未确认')
            self.s.click_point(self.account,(box[0]+box[2])//2,(box[1]+box[3])//2,label,
                               [TITLE,'grocery_close_current.bmp'],frame_predicate=verified)
            return
        ok, _, x, y = self.s.template(name, roi, frame, threshold=.96)
        if not ok:
            raise hs.PendingError('杂货屋控件未确认：' + name)
        with hs.Image.open(hs.ASSETS / name) as im:
            width, height = im.size
        self.s.click_point(self.account, x + width//2, y + height//2, label, [TITLE],
                           irreversible=irreversible, frame_predicate=lambda f:
                           self.s.template(name, (x-2,y-2,x+width+2,y+height+2), f, threshold=.96)[0]
                           and (predicate(f) if predicate else True))

    def open(self):
        frame = self.s.capture()
        if not self.ready(frame):
            if not self.s.main_ready(frame):
                raise hs.PendingError('杂货屋入口前尚未确认主界面')
            ok,_,tx,ty=self.s.template('grocery_toolbar.bmp',(1400,890,1910,1030),frame,threshold=.96)
            if ok:
                self.s.click_point(self.account,tx+28,ty+25,'grocery_entry',['anchor_top_menu.bmp'],
                                   frame_predicate=lambda f:self.s.template('grocery_toolbar.bmp',(tx-2,ty-2,tx+60,ty+65),f,threshold=.96)[0])
                if not self.s.wait_template(TITLE,(800,80,1110,190),12):raise hs.PendingError('杂货屋页面未打开')
                self.click_asset('grocery_moon_tab.bmp',(1310,110,1470,205),'grocery_moon_tab')
                self.s.wait_interruptible(.4)
                return
            from hyol_ocr import recognize
            box = (1400, 890, 1910, 1030)
            data = recognize(frame.crop(box))
            found = []
            for line in data.get('lines', []):
                words = line.get('words', [])
                if re.sub(r'\s+', '', line.get('text', '')) == '杂货屋' and words:
                    x = min(w['x'] for w in words) + box[0]
                    y = min(w['y'] for w in words) + box[1]
                    found.append((int(x+20), int(y+5)))
            if len(found) != 1:
                raise hs.PendingError('主界面杂货屋按钮未唯一识别')
            self.s.click_point(self.account, *found[0], 'grocery_entry', ['anchor_top_menu.bmp'])
            if not self.s.wait_template(TITLE, (800,80,1110,190), 12):
                raise hs.PendingError('杂货屋页面未打开')
        self.click_asset('grocery_moon_tab.bmp', (1310,110,1470,205), 'grocery_moon_tab')
        self.s.wait_interruptible(.4)

    def balance(self, frame):
        ok, _, x, y = self.s.template('grocery_moon_balance.bmp', (640,190,820,245), frame, threshold=.96)
        if not self.ready(frame) or not ok:
            return None
        return number(frame.crop((x+29,y+5,x+94,y+22)),threshold=210)

    def offer(self, code, frame):
        ok, _, x, y = self.s.template('grocery_item_'+code+'.bmp', BODY, frame, threshold=.96)
        if not self.ready(frame) or not ok:
            return None
        if y+115>958:return None
        good, _, cx, cy = self.s.template('grocery_moon_price.bmp', (x+40,y-10,x+135,y+45), frame, threshold=.96)
        if not good:
            raise hs.PendingError(ITEMS[code][0]+'未确认使用月之印')
        price = number(frame.crop((cx+27,cy-3,cx+90,cy+23)))
        stock = stock_number(text(frame.crop((cx-2,cy+23,cx+94,cy+50))))
        if stock is None and self.s.template('grocery_stock_label.bmp',(cx-5,cy+20,cx+40,cy+51),frame,threshold=.96)[0]:
            stock=number(frame.crop((cx+29,cy+23,cx+94,cy+50)))
        if price != ITEMS[code][1] or stock is None:
            raise hs.PendingError(ITEMS[code][0]+'价格或库存未确认，未提交购买')
        return dict(code=code, x=x, y=y, price=price, stock=stock)

    def seek(self, code):
        # Inspect the current shelf first; adjacent selected goods share a page.
        current=self.offer(code,self.s.capture())
        if current is not None:return current
        for direction in ('down', 'up'):
            previous = None
            for index in range(64):
                from hyol_identity_overlays import clear_hover_preview
                clear_hover_preview(self.s)
                frame = self.s.capture()
                offer = self.offer(code, frame)
                if offer is not None:
                    return offer
                current = hs.image_hash(frame.crop(BODY))
                if current == previous:
                    break
                previous = current
                self.click_asset('grocery_scroll_'+direction+'.bmp', (1345,248,1380,975), 'grocery_scroll_'+direction)
                self.s.wait_interruptible(.15)
        raise TaskUnavailable(ITEMS[code][0]+'当前商品列表中未找到，已保留未购买状态')

    def set_quantity(self, offer, quantity):
        x, y = offer['x'], offer['y']
        box = (x-25,y+69,x+29,y+105)
        current = number(self.s.capture().crop(box))
        if current is None or not 1 <= current <= 999:
            raise hs.PendingError('购买数量框未识别，未点击购买')
        direction = 'up' if current < quantity else 'down'
        for _ in range(abs(quantity-current)):
            self.click_asset('grocery_quantity_'+direction+'.bmp', (x+24,y+60,x+65,y+112),
                             'grocery_quantity_'+direction,
                             predicate=lambda f: self.offer(offer['code'], f) is not None)
        if number(self.s.capture().crop(box)) != quantity:
            raise hs.PendingError('购买数量未达到所选库存，未提交购买')

    def confirmation(self, frame, quote):
        from hyol_ocr import recognize
        box = (550,300,1390,820)
        data = recognize(frame.crop(box))
        content = re.sub(r'\s+', '', data.get('text', ''))
        name = ITEMS[quote['code']][0]
        qty, total = quote['quantity'], quote['quantity'] * quote['price']
        if (name not in content or '月之印' not in content
                or not re.search(rf'(?<!\d){qty}(?!\d)', content)
                or not re.search(rf'(?<!\d){total}(?!\d)', content)):
            return None
        buttons = []
        for line in data.get('lines', []):
            if re.sub(r'\s+', '', line.get('text','')) not in ('确定', '确认购买'):
                continue
            words = line.get('words', [])
            if words:
                buttons.append((int(box[0]+min(w['x'] for w in words)+15),
                                int(box[1]+min(w['y'] for w in words)+8)))
        return buttons[0] if len(buttons) == 1 else None

    def settle(self, tx):
        if tx.get('phase')=='done':return
        quote, code = tx['quote'], tx['quote']['code']
        for _ in range(8):
            self.check()
            frame = self.s.content_guard(self.account,[TITLE])
            point = self.confirmation(frame, quote)
            if point:
                if self.s.state.action_status(self.account.name, tx['action']) == 'pending':
                    self.s.commit_action(self.account, tx['action'], '已确认原购买报价弹窗，尚未确认付款')
                confirm = tx['action']+'_confirm'
                if self.s.state.action_status(self.account.name, confirm) == 'new':
                    tx['phase'] = 'confirming'; self.save()
                    self.s.click_point(self.account, *point, confirm, [TITLE], irreversible=True,
                                       frame_predicate=lambda f: self.confirmation(f, quote) == point)
                    self.save()
            else:
                try:
                    offer = self.offer(code, frame)
                    balance = self.balance(frame)
                except hs.PendingError:
                    self.s.wait_interruptible(.4)
                    continue
                if reconciled(quote, offer, balance):
                    self.s.wait_interruptible(.3)
                    fresh = self.s.capture()
                    try:
                        second_verified=reconciled(quote,self.offer(code,fresh),self.balance(fresh))
                    except hs.PendingError:
                        second_verified=False
                    if (hs.image_hash(frame.crop((714,209,779,226))) != hs.image_hash(fresh.crop((714,209,779,226)))
                            or not self.s.verify_identity(self.account,fresh)[0] or not second_verified):
                        continue
                    proof = str(self.s.save_raw_evidence(fresh, self.account, 'grocery_'+code+'_purchased', 'verified'))
                    for action in (tx['action'], tx['action']+'_confirm'):
                        if self.s.state.action_status(self.account.name, action) == 'pending':
                            self.s.commit_action(self.account, action, '库存与月之印余额变化一致：'+proof)
                    tx.update(phase='done', evidence=proof, balance_after=balance, stock_after=offer['stock'])
                    self.rec['purchased'][code] = self.rec['purchased'].get(code, 0)+quote['quantity']
                    self.save()
                    original=tx.get('origin_run')!=getattr(getattr(self.s,'diagnostics',None),'identifier',None)
                    self.r.event('grocery_purchase_reconciled' if original else 'grocery_purchase_verified',
                                 task='grocery', item=code, quantity=quote['quantity'],evidence=proof,
                                 detail=f'{ITEMS[code][0]}'+('原购买' if original else '本次购买')+f'{quote["quantity"]}个，库存和月之印扣款已核实')
                    return
            self.s.wait_interruptible(.4)
        raise hs.PendingError('杂货屋原购买结果尚未核实，保留原凭据，不重复购买')

    def run(self):
        if not self.selected:
            raise TaskUnavailable('杂货屋尚未选择商品；请在桌面二级设置中选择，本次未购买')
        from hyol_current_team_state import CurrentTeamLedger
        self.ledger = CurrentTeamLedger(self.r, 'grocery', dict(currency='moon'))
        self.rec = self.ledger.record
        self.rec.update(task_completed=False, home_returned=False, phase='shopping', status='running')
        self.rec.setdefault('transactions', [])
        self.rec.setdefault('purchased', {})
        self.rec['unavailable_items'] = {}
        self.rec['selected_items'] = list(self.selected)
        self.rec['evaluated_items'] = {}
        self.rec['budget_skipped_items'] = {}
        self.save()
        pending = [tx for tx in self.rec['transactions'] if tx.get('phase') not in ('done','not_sent')]
        if len(pending)>1:raise hs.PendingError('存在多项原购买未决，保留全部凭据并停止购买')
        if pending:
            tx=pending[0]
            if (self.s.state.action_status(self.account.name,tx['action'])=='new'
                    and self.s.state.action_status(self.account.name,tx['action']+'_confirm')=='new'):
                tx.update(phase='not_sent',reason='原意图保存后没有发送购买输入，重新核对当前报价')
                self.save()
            else:
                frame=self.s.capture()
                if not self.ready(frame) and self.s.main_ready(frame):
                    self.open();self.seek(tx['quote']['code'])
                self.settle(tx)
        self.open()
        for code in self.selected:
            self.s.check_stop()
            try:
                offer = self.seek(code)
                frame=self.s.content_guard(self.account,[TITLE]);balance = self.balance(frame)
                before_balance=hs.image_hash(frame.crop((714,209,779,226)))
                self.s.wait_interruptible(.3)
                fresh=self.s.capture()
                if balance is None or self.balance(fresh)!=balance or hs.image_hash(fresh.crop((714,209,779,226)))!=before_balance:
                    raise hs.PendingError('月之印余额未稳定，等待公告或遮挡消失后继续；未购买')
                quantity = purchase_quantity(offer['stock'], balance, offer['price'])
                if not quantity:
                    current=self.offer(code,fresh)
                    if (current is None or current['stock']!=offer['stock'] or current['price']!=offer['price']):
                        raise hs.PendingError('跳过购买前库存或价格变化，保留本项待核实')
                    reason='stock_empty' if offer['stock']==0 else 'insufficient_balance'
                    proof=str(self.s.save_raw_evidence(fresh,self.account,'grocery_'+code+'_'+reason,'verified'))
                    evaluated=dict(reason=reason,stock=offer['stock'],price=offer['price'],balance=balance,
                                   quantity=0,evidence=proof)
                    self.rec['evaluated_items'][code]=evaluated
                    if reason=='insufficient_balance':self.rec['budget_skipped_items'][code]=evaluated
                    self.save()
                    self.r.event('grocery_stock_empty' if reason=='stock_empty' else 'grocery_budget_skipped',
                        task='grocery',item=code,evidence=proof,
                        detail=ITEMS[code][0]+('库存已为零' if reason=='stock_empty' else '整项库存总价超过月之印余额，已按设置跳过，未购买'))
                    continue
                self.set_quantity(offer, quantity)
                fresh = self.s.capture()
                current = self.offer(code, fresh)
                if current is None or current['stock'] != offer['stock'] or self.balance(fresh) != balance:
                    raise hs.PendingError('购买前库存或余额变化，未按旧报价下单')
                quote = dict(code=code, stock=offer['stock'], price=offer['price'], balance=balance, quantity=quantity,
                             balance_hash=hs.image_hash(fresh.crop((714,209,779,226))))
                tx = dict(quote=quote, phase='intent', action='grocery_buy_'+code+'_'+uuid.uuid4().hex[:12],
                          origin_run=self.s.diagnostics.identifier,
                          before_evidence=str(self.s.save_raw_evidence(fresh,self.account,'grocery_'+code+'_quote','before')))
                self.rec['transactions'].append(tx); self.save()
                self.click_asset('grocery_buy.bmp', (offer['x']+50,offer['y']+60,offer['x']+165,offer['y']+115),
                                 tx['action'], irreversible=True,
                                 predicate=lambda f: (self.offer(code,f) or {}).get('stock') == quote['stock']
                                 and self.balance(f) == quote['balance']
                                 and hs.image_hash(f.crop((714,209,779,226)))==quote['balance_hash']
                                 and number(f.crop((offer['x']-25,offer['y']+69,offer['x']+29,offer['y']+105)))==quantity)
                self.save()
                self.settle(tx)
                self.rec['evaluated_items'][code]=dict(reason='purchased',quantity=quote['quantity'],
                    stock=quote['stock'],price=quote['price'],balance=quote['balance'],
                    evidence=tx['evidence'],balance_after=tx['balance_after'])
                self.save()
            except TaskUnavailable as exc:
                self.rec['unavailable_items'][code] = str(exc); self.save()
                self.r.event('grocery_item_unavailable', task='grocery', item=code, detail=str(exc))
        self.click_asset('grocery_close_current.bmp', (1570,110,1660,205), 'grocery_close')
        self.r.return_to_main()
        self.rec.update(home_returned=True, phase='ready')
        self.save()
        if self.rec['unavailable_items']:
            raise TaskUnavailable('；'.join(self.rec['unavailable_items'].values()))
        if set(self.rec['evaluated_items'])!=set(self.selected):
            raise hs.PendingError('所选商品尚未逐项核实，保留待处理')


def run(runner):
    raise TaskUnavailable('杂货屋购买已按用户要求暂缓，本版不执行购买；原报价、扣款凭据和未决记录保留')


def _run_preserved_purchase(runner):
    # The quantity spinner changes only a few glyphs, below the generic full
    # screen idle detector. This driver has bounded scroll/quantity loops and
    # its own deadline; identity, foreground and F12 guards remain active.
    scope=getattr(runner.scheduler,'_task_observation',None)
    runner.scheduler._task_observation=None
    try:return Grocery(runner).run()
    finally:
        if isinstance(scope,tuple):
            scope[2].reference=None;scope[2].changed_at=None
        runner.scheduler._task_observation=scope
