import { CalendarAgenda } from './src/CalendarAgenda';
import { ConfluenceView } from './src/ConfluenceView';
import { AIResponse } from './src/AIResponse';
import { MarketAnalysisView } from './src/MarketAnalysisView';
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, AppState, Image, KeyboardAvoidingView, Platform, Pressable, RefreshControl, ScrollView, StatusBar, StyleSheet, Switch, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import * as SecureStore from 'expo-secure-store';
import { Connection, loadConnection, request, saveConnection, validateConnection } from './src/api';
import { registerPush } from './src/push';
import { Button, Busy, C, CandleChart, Card, Chips, Empty, Field, Icon, Result, s } from './src/ui';

type Tab = 'home' | 'market' | 'ai' | 'tools' | 'inbox' | 'settings';
type Run = (fn: () => Promise<void>) => Promise<void>;
const tabs: { key: Tab; label: string; icon: any }[] = [
  { key: 'home', label: 'Beranda', icon: 'grid-outline' }, { key: 'market', label: 'Pasar', icon: 'stats-chart-outline' },
  { key: 'ai', label: 'Assistant', icon: 'sparkles-outline' }, { key: 'tools', label: 'Toolkit', icon: 'options-outline' },
  { key: 'inbox', label: 'Inbox', icon: 'notifications-outline' },
];

export default function App() { return <SafeAreaProvider><Shell /></SafeAreaProvider>; }

function Shell() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [booting, setBooting] = useState(true);
  const [tab, setTab] = useState<Tab>('home');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const run: Run = async fn => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try { await fn(); } catch (e: any) { setError(e.message || 'Koneksi gagal. Periksa alamat server dan jaringan.'); }
    finally { inFlight.current = false; setBusy(false); }
  };
  useEffect(() => { loadConnection().then(setConnection).catch(() => setError('Koneksi tersimpan tidak dapat dibaca. Silakan masuk kembali.')).finally(() => setBooting(false)); }, []);
  async function connect(c: Connection) { const valid = validateConnection(c); await request(valid, '/health'); await saveConnection(valid); setConnection(valid); }
  async function disconnect() { await saveConnection(null); setConnection(null); setTab('home'); }
  if (booting) return <View style={[styles.root, { justifyContent: 'center' }]}><ActivityIndicator color={C.gold} /></View>;
  return <SafeAreaView style={styles.root} edges={['top', 'bottom']}><StatusBar barStyle="light-content" />
    <View style={styles.frame}>
      <View style={styles.header}><View style={s.row}><Image source={require('./assets/bayproject-logo.jpeg')} accessibilityLabel="Logo Bayproject FX" style={styles.logo} resizeMode="contain" /><View><Text style={styles.brand}>BAYPROJECT<Text style={{ color: C.silver }}>.FX</Text></Text><Text style={styles.brandSub}>TRADE  |  ANALYZE  |  ACHIEVE</Text></View></View>
        {connection && <Pressable accessibilityLabel="Pengaturan" accessibilityRole="button" disabled={busy} onPress={() => setTab('settings')} style={styles.iconButton}><Icon name="settings-outline" color={tab === 'settings' ? C.gold : C.muted} /></Pressable>}
      </View>
      {!!error && <View style={styles.error}><Icon name="alert-circle-outline" color={C.red} /><Text style={{ color: C.red, flex: 1, lineHeight: 20 }}>{error}</Text><Pressable accessibilityRole="button" accessibilityLabel="Tutup pesan error" onPress={() => setError('')}><Icon name="close" color={C.red} size={18} /></Pressable></View>}
      {busy && <View style={styles.progress}><ActivityIndicator size="small" color={C.gold} /><Text style={s.muted}>Memproses permintaan… Server gratis mungkin sedang dibangunkan.</Text></View>}
      {!connection ? <Connect onConnect={c => run(() => connect(c))} busy={busy} /> : <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        {tab === 'home' && <Home connection={connection} run={run} busy={busy} navigate={setTab} />}
        {tab === 'market' && <Market connection={connection} run={run} busy={busy} />}
        {tab === 'ai' && <Chat connection={connection} run={run} busy={busy} />}
        {tab === 'tools' && <Tools connection={connection} run={run} busy={busy} />}
        {tab === 'inbox' && <Inbox connection={connection} run={run} busy={busy} />}
        {tab === 'settings' && <Settings connection={connection} run={run} busy={busy} disconnect={disconnect} />}
      </KeyboardAvoidingView>}
      {connection && <View style={styles.tabs}>{tabs.map(t => <Pressable accessibilityRole="tab" accessibilityState={{ selected: tab === t.key }} disabled={busy} key={t.key} onPress={() => { setError(''); setTab(t.key); }} style={styles.tab}><Icon name={t.icon} color={tab === t.key ? C.gold : C.muted} /><Text style={{ color: tab === t.key ? C.gold : C.muted, fontSize: 10, fontWeight: '600' }}>{t.label}</Text>{tab === t.key && <View style={styles.tabDot} />}</Pressable>)}</View>}
    </View>
  </SafeAreaView>;
}

function Connect({ onConnect, busy }: { onConnect: (c: Connection) => void; busy: boolean }) {
  const [url, setUrl] = useState(''), [token, setToken] = useState('');
  return <ScrollView contentContainerStyle={[s.page, { paddingTop: 34 }]} keyboardShouldPersistTaps="handled">
    <Image source={require('./assets/bayproject-logo.jpeg')} accessibilityLabel="Bayproject FX — XAUUSD Scalper" style={{ width: '100%', height: 250, backgroundColor: '#000' }} resizeMode="contain" />
    <View style={[s.row, { gap: 6 }]}><View style={styles.dot} /><Text style={[s.label, { color: C.gold }]}>PRIVATE EDITION / 01</Text></View>
    <Text style={[s.title, { fontSize: 43, lineHeight: 48 }]}>Pasar bergerak.{'\n'}Tetap selangkah{'\n'}<Text style={{ color: C.gold }}>lebih siap.</Text></Text>
    <Text style={[s.muted, { fontSize: 15, lineHeight: 24 }]}>Analisis pasar, AI assistant, dan alert pribadi. Semua perangkat trading kamu, dalam satu tempat.</Text>
    <View style={[s.row, { flexWrap: 'wrap' }]}>{['Forex & Gold', 'AI Agent', 'Smart Alerts'].map(t => <View key={t} style={s.chip}><Text style={s.muted}>{t}</Text></View>)}</View>
    <Card><View style={s.between}><Text style={s.heading}>Hubungkan workspace</Text><Icon name="lock-closed-outline" color={C.gold} /></View>
      <Text style={s.muted}>Masukkan alamat backend pribadi dan kunci akses dari konfigurasi server kamu.</Text>
      <Field label="Alamat server" value={url} onChangeText={setUrl} placeholder="https://api.example.com" />
      <Field label="Kunci akses pribadi" value={token} onChangeText={setToken} secure placeholder="APP_ACCESS_TOKEN" />
      <Button title="Masuk ke workspace" icon="arrow-forward" disabled={busy || !url || !token} onPress={() => onConnect({ url: url.trim(), token: token.trim() })} />
      <Text style={[s.muted, { fontSize: 11 }]}>Kunci akses disimpan aman di perangkat. API key AI tetap berada di server.</Text>
    </Card><Text style={[s.label, { textAlign: 'center', marginTop: 8 }]}>BUILT AROUND YOUR EDGE</Text>
  </ScrollView>;
}

type ScreenProps = { connection: Connection; run: Run; busy: boolean };
function Heading({ eyebrow, title, detail }: { eyebrow: string; title: string; detail?: string }) { return <View style={{ gap: 7 }}><Text style={s.label}>{eyebrow}</Text><Text style={s.title}>{title}</Text>{detail && <Text style={s.muted}>{detail}</Text>}</View>; }

function Home({ connection, run, busy, navigate }: ScreenProps & { navigate: (t: Tab) => void }) {
  const [watch, setWatch] = useState<any[]>([]), [quotes, setQuotes] = useState<any[]>([]), [symbol, setSymbol] = useState('XAUUSD');
  const [loaded, setLoaded] = useState(false);
  const refresh = async () => {
    const rows = await request(connection, '/watchlist'); setWatch(rows); setLoaded(true);
    const prices = await Promise.all(rows.map(async (r: any) => {
      try { return await request(connection, '/market/price', 'POST', r); }
      catch (e: any) { return { symbol: r.symbol, error: e.message }; }
    })); setQuotes(prices);
  };
  useEffect(() => { void run(refresh); }, []);
  return <ScrollView contentContainerStyle={s.page} refreshControl={<RefreshControl refreshing={busy} onRefresh={() => run(refresh)} tintColor={C.gold} />} keyboardShouldPersistTaps="handled">
    <Heading eyebrow="PERSONAL WORKSPACE" title="Market overview" detail="Fokus pada setup. Biarkan data memberi konteks." />
    <Card style={{ backgroundColor: '#10100e', borderColor: '#655332' }}><Image source={require('./assets/bayproject-logo.jpeg')} accessibilityLabel="Bayproject FX — XAUUSD Scalper" style={{ width: '100%', height: 230, backgroundColor: '#000', borderRadius: 12 }} resizeMode="contain" /><View style={s.between}><Text style={[s.label, { color: C.gold }]}>YOUR PRIVATE TRADING DESK</Text><Icon name="trending-up" color={C.gold} /></View><Text style={[s.title, { fontSize: 25 }]}>Konteks yang lebih lengkap.</Text><Text style={s.muted}>Baca struktur pasar, cek confluence, lalu diskusikan setup dengan AI.</Text><Button title="Buka analisis pasar" icon="arrow-forward" onPress={() => navigate('market')} disabled={busy} /></Card>
    <View style={s.between}><Text style={s.heading}>Watchlist kamu</Text><Text style={s.label}>{watch.length} PAIR</Text></View>
    {!loaded && <Busy />}
    {loaded && watch.length === 0 && <Card><Empty title="Mulai dari pair pilihanmu" detail="Tambahkan pair untuk memantau harga dan mengaktifkan pemindaian setup di server." icon="add-circle-outline" /></Card>}
    {quotes.map(q => <Card key={q.symbol}><View style={s.between}><View style={s.row}><View style={styles.pairBadge}><Text style={{ color: C.gold, fontWeight: '700' }}>{q.symbol.slice(0, 3)}</Text></View><View><Text style={s.heading}>{q.symbol}</Text><Text style={s.muted}>{q.source || 'Sumber tidak tersedia'}</Text></View></View><View style={{ alignItems: 'flex-end' }}><Text style={[s.heading, { fontSize: 23 }]}>{q.price ? Number(q.price).toLocaleString('en-US', { maximumFractionDigits: 5 }) : '—'}</Text><Pressable accessibilityLabel={`Hapus ${q.symbol}`} disabled={busy} onPress={() => run(async () => { await request(connection, '/watchlist', 'DELETE', { symbol: q.symbol }); await refresh(); })}><Text style={[s.muted, { color: C.red }]}>Hapus</Text></Pressable></View></View>{q.error ? <Text style={{ color: C.red }}>{q.error}</Text> : <Text style={[s.muted, { fontSize: 11 }]}>Diambil {new Date(q.fetched_at).toLocaleString('id-ID')} · Tarik untuk memperbarui</Text>}</Card>)}
    <Card><Field label="Tambahkan pair" value={symbol} onChangeText={setSymbol} placeholder="EURUSD" /><Button title="Tambah ke watchlist" secondary icon="add" disabled={busy} onPress={() => run(async () => { await request(connection, '/watchlist', 'POST', { symbol }); await refresh(); })} /></Card>
    <View style={s.row}><View style={{ flex: 1 }}><Button title="AI Assistant" secondary icon="sparkles-outline" disabled={busy} onPress={() => navigate('ai')} /></View><View style={{ flex: 1 }}><Button title="Trading tools" secondary icon="options-outline" disabled={busy} onPress={() => navigate('tools')} /></View></View>
    <Text style={s.muted}>Scanner memeriksa watchlist setiap ±15 menit saat server aktif. Harga ditampilkan sesuai waktu pengambilan, bukan streaming.</Text>
  </ScrollView>;
}

function Market({ connection, run, busy }: ScreenProps) {
  const [symbol, setSymbol] = useState('XAUUSD'), [interval, setInterval] = useState('1h');
  const [data, setData] = useState<any>(null), [extra, setExtra] = useState<any>(null), [chart, setChart] = useState('');
  const [detail, setDetail] = useState('Ringkasan');
  const body = { symbol, interval };
  const load = () => run(async () => { setData(null); setExtra(null); setChart(''); setData(await request(connection, '/market/analysis', 'POST', body)); });
  return <ScrollView contentContainerStyle={s.page} keyboardShouldPersistTaps="handled"><Heading eyebrow="MARKET INTELLIGENCE" title="Baca struktur pasar" />
    <Card><Field label="Pair" value={symbol} onChangeText={v => { setSymbol(v); setData(null); setExtra(null); setChart(''); }} /><Chips values={['1min', '5min', '15min', '1h', '4h', '1day']} value={interval} onChange={v => { setInterval(v); setData(null); setExtra(null); setChart(''); }} /><Button title="Ambil analisis" icon="analytics-outline" disabled={busy} onPress={load} /></Card>
    {data ? <><Card><View style={s.between}><Text style={s.heading}>{data.symbol}</Text><Text style={[s.label, { color: C.gold }]}>{data.interval}</Text></View><View style={s.between}><Text style={[s.title, { fontSize: 25 }]}>{data.confidence?.bias_direction || 'Netral'}</Text><View><Text style={[s.title, { color: C.gold, textAlign: 'right' }]}>{data.confidence?.confidence ?? '—'}<Text style={{ fontSize: 16 }}>/100</Text></Text><Text style={s.muted}>Technical score</Text></View></View><Text style={s.muted}>{data.regime} · Skor teknikal bukan probabilitas profit.</Text><CandleChart candles={data.candles || []} /><Text style={[s.muted, { fontSize: 11 }]}>Data diambil {new Date(data.fetched_at).toLocaleString('id-ID')}</Text></Card>
      <Chips values={['Ringkasan', 'Indikator', 'Level S/R', 'Pattern', 'Structure', 'Score']} value={detail} onChange={setDetail} />
      <Card><MarketAnalysisView type={detail} data={detail === 'Indikator' ? data.indicators : detail === 'Level S/R' ? data.sr : detail === 'Pattern' ? data.pattern : detail === 'Structure' ? data.structure : detail === 'Score' ? data.confidence : { regime: data.regime, trap: data.trap }} /></Card>
      <Button title="Chart lengkap + EMA / S/R" secondary disabled={busy} onPress={() => run(async () => setChart((await request(connection, '/market/chart', 'POST', body)).image))} />
      {!!chart && <Card><Image accessibilityLabel="Chart lengkap EMA dan support resistance" source={{ uri: `data:image/png;base64,${chart}` }} style={{ width: '100%', aspectRatio: 1.5 }} resizeMode="contain" /></Card>}
    </> : <Card><Empty title="Analisis dimulai dari data" detail="Pilih pair dan timeframe untuk melihat chart, indikator, pola, serta level penting." icon="stats-chart-outline" /></Card>}
    <Button title="Cek confluence 4 timeframe" secondary disabled={busy} onPress={() => run(async () => setExtra(await request(connection, '/market/confluence', 'POST', { symbol })))} />
    <Button title="Penjelasan teknikal oleh AI" secondary disabled={busy} onPress={() => run(async () => setExtra(await request(connection, '/market/explain', 'POST', body)))} />
    {extra && <Card><Text style={s.heading}>{Array.isArray(extra.per_tf) ? 'Confluence 4 timeframe' : 'Analisis lanjutan'}</Text>{Array.isArray(extra.per_tf) ? <ConfluenceView data={extra} /> : <Result data={extra} />}</Card>}
  </ScrollView>;
}

function Chat({ connection, run, busy }: ScreenProps) {
  const [messages, setMessages] = useState<any[]>([]), [message, setMessage] = useState(''), [image, setImage] = useState<{ uri: string; base64: string } | null>(null);
  const scroll = useRef<ScrollView>(null);
  useEffect(() => {
    const frame = requestAnimationFrame(() => scroll.current?.scrollToEnd({ animated: true }));
    return () => cancelAnimationFrame(frame);
  }, [messages.length]);
  useEffect(() => { void run(async () => setMessages(await request(connection, '/chat'))); }, []);
  const send = () => run(async () => {
    await request(connection, '/chat', 'POST', { message: message.trim() || 'Analisis screenshot chart ini.', ...(image ? { image: image.base64 } : {}) });
    setMessage(''); setImage(null); setMessages(await request(connection, '/chat'));
  });
  const pick = () => run(async () => {
    const selected = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], base64: true, quality: .8 });
    if (!selected.canceled && selected.assets[0].base64) {
      const asset = selected.assets[0];
      if ((asset.base64!.length * .75) > 5_000_000) throw new Error('Gambar maksimal 5 MB. Gunakan screenshot lebih kecil.');
      setImage({ uri: asset.uri, base64: asset.base64! });
    }
  });
  return <View style={{ flex: 1 }}><ScrollView ref={scroll} contentContainerStyle={s.page} keyboardShouldPersistTaps="handled">
    <Heading eyebrow="AI ASSISTANT" title="Partner analisismu." detail="Diskusikan setup, baca chart, dan atur alert lewat percakapan." />
    {!messages.length && <><Card><Icon name="sparkles" color={C.gold} size={30} /><Text style={s.heading}>Apa yang ingin kamu pahami?</Text><Text style={s.muted}>AI menggunakan konteks pasar untuk pertanyaan harga, kalender, berita, dan regime. Aksi watchlist dan alert tersimpan di workspace kamu.</Text></Card>{['Bagaimana regime XAUUSD saat ini?', 'Tambahkan EURUSD ke watchlist', 'Aktifkan daily debrief'].map(t => <Button key={t} title={t} secondary disabled={busy} onPress={() => setMessage(t)} />)}</>}
    {messages.map((m, i) => <View key={m.id || i} style={[s.card, m.role === 'user' ? { backgroundColor: '#292419', borderColor: '#5d4d2e', marginLeft: 30 } : { marginRight: 0 }]}><Text style={[s.label, { color: m.role === 'user' ? C.muted : C.gold }]}>{m.role === 'user' ? 'KAMU' : 'BAYPROJECT AI'}</Text><AIResponse text={m.content} /></View>)}
    {busy && <Busy label="AI sedang menyusun jawaban…" />}
  </ScrollView><View style={styles.composer}>{image && <View style={s.row}><Image source={{ uri: image.uri }} style={{ width: 54, height: 54, borderRadius: 8 }} /><Text style={[s.muted, { flex: 1 }]}>Screenshot siap dianalisis</Text><Button title="Hapus" secondary onPress={() => setImage(null)} disabled={busy} /></View>}<Field label="Pesan" value={message} onChangeText={setMessage} placeholder="Tanyakan setup atau buat alert…" multiline /><View style={s.between}><Button title="Chart" secondary icon="image-outline" disabled={busy} onPress={pick} /><Button title="Kirim" icon="arrow-up" disabled={busy || (!message.trim() && !image)} onPress={send} /></View></View></View>;
}

const toolOptions = ['Sinyal', 'Backtest', 'Kalkulator lot', 'Korelasi', 'Kalender', 'Berita & Makro'];
function Tools({ connection, run, busy }: ScreenProps) {
  const [tool, setTool] = useState('Sinyal'), [symbol, setSymbol] = useState('XAUUSD'), [interval, setInterval] = useState('1h');
  const [candles, setCandles] = useState('500'), [balance, setBalance] = useState('1000'), [risk, setRisk] = useState('1'), [sl, setSl] = useState('20');
  const [pairs, setPairs] = useState('XAUUSD, EURUSD, GBPUSD, USDJPY'), [query, setQuery] = useState('gold USD forex');
  const [showEventTools, setShowEventTools] = useState(false);
  const [high, setHigh] = useState(false), [today, setToday] = useState(false), [event, setEvent] = useState('CPI');
  const [output, setOutput] = useState<any>(null), [outputTitle, setOutputTitle] = useState('');
  const [reports, setReports] = useState<Record<string, any>>({});
  const call = (title: string, path: string, body?: any, method = 'POST') => run(async () => {
    setOutput(null); setOutputTitle(title);
    const result = await request(connection, path, method, body);
    setOutput(result);
    if (['Macro briefing', 'Daily debrief', 'Berita terbaru'].includes(title)) {
      setReports(previous => ({ ...previous, [title]: result }));
    }
  });
  return <ScrollView contentContainerStyle={s.page} keyboardShouldPersistTaps="handled"><Heading eyebrow="TRADER TOOLKIT" title="Dari konteks ke keputusan." /><Chips values={toolOptions} value={tool} onChange={t => { setTool(t); setOutput(null); }} />
    <Card><Text style={s.heading}>{tool}</Text>
      {['Sinyal', 'Backtest', 'Kalkulator lot'].includes(tool) && <Field label="Pair" value={symbol} onChangeText={setSymbol} />}
      {['Sinyal', 'Backtest'].includes(tool) && <Chips values={['15min', '1h', '4h', '1day']} value={interval} onChange={setInterval} />}
      {tool === 'Sinyal' && <><Text style={s.muted}>Scanner berbasis aturan bot. Keputusan entry khusus gold memakai M15, H4, dan konteks fundamental.</Text><Button title="Scan setup pair" disabled={busy} onPress={() => call('Scanner', '/signals/scan', { symbol, interval })} /><Button title="Keputusan entry XAU/USD" secondary disabled={busy} onPress={() => call('Entry XAU/USD', '/signals/entry')} /></>}
      {tool === 'Backtest' && <><Field label="Jumlah candle (120–1000)" value={candles} onChangeText={setCandles} numeric /><Text style={s.muted}>Simulasi historis strategi scanner asli bot. Hasil belum memasukkan seluruh biaya eksekusi broker.</Text><Button title="Jalankan backtest" disabled={busy} onPress={() => call('Hasil backtest', '/signals/backtest', { symbol, interval, candles: Number(candles) })} /></>}
      {tool === 'Kalkulator lot' && <><Field label="Balance (USD)" value={balance} onChangeText={setBalance} numeric /><Field label="Risiko (%)" value={risk} onChangeText={setRisk} numeric /><Field label="Stop loss (pips)" value={sl} onChangeText={setSl} numeric /><Button title="Hitung ukuran posisi" disabled={busy} onPress={() => call('Ukuran posisi', '/tools/lot', { symbol, balance: Number(balance), risk_percent: Number(risk), sl_pips: Number(sl) })} /><Text style={s.muted}>Estimasi dari bot; sesuaikan dengan spesifikasi kontrak broker.</Text></>}
      {tool === 'Korelasi' && <><Field label="Pair dipisahkan koma (2–6)" value={pairs} onChangeText={setPairs} multiline /><Button title="Hitung korelasi" disabled={busy} onPress={() => call('Matriks korelasi', '/tools/correlation', { symbols: pairs.split(',').map(p => p.trim()).filter(Boolean) })} /></>}
      {tool === 'Kalender' && <><View style={s.between}><Text style={s.text}>High impact saja</Text><Switch accessibilityLabel="High impact saja" value={high} onValueChange={setHigh} trackColor={{ true: C.gold }} /></View><View style={s.between}><Text style={s.text}>Hari ini saja</Text><Switch accessibilityLabel="Hari ini saja" value={today} onValueChange={setToday} trackColor={{ true: C.gold }} /></View><Button title="Muat kalender USD" disabled={busy} onPress={() => call('Kalender ekonomi', `/calendar?high=${high}&today=${today}`, undefined, 'GET')} /><Button title={showEventTools ? "Tutup alat analisis event" : "Analisis event & cari actual"} secondary onPress={() => setShowEventTools(!showEventTools)} />{showEventTools && <><Field label="Nama event untuk analisis" value={event} onChangeText={setEvent} placeholder="CPI / Non-Farm / FOMC" /><Button title="Preview & prediksi event" secondary disabled={busy} onPress={() => call('Event preview', '/calendar/preview', { query: event })} /><Button title="Analisis bias fundamental" secondary disabled={busy} onPress={() => call('Bias fundamental', '/calendar/bias', { query: event })} /><Button title="Cari hasil actual tambahan" secondary disabled={busy} onPress={() => call('Actual event', '/calendar/actual')} /></>}</>}
      {tool === 'Berita & Makro' && <><Field label="Topik berita" value={query} onChangeText={setQuery} /><Button title="Cari & rangkum berita" disabled={busy} onPress={() => call('Berita terbaru', '/reports/news', { query })} /><Button title="Macro briefing" secondary disabled={busy} onPress={() => call('Macro briefing', '/reports/macro', { query })} /><Button title="Daily market debrief" secondary disabled={busy} onPress={() => call('Daily debrief', '/reports/debrief', { query })} /></>}
     </Card>
     {Object.keys(reports).length > 0 && <Card><View style={s.row}><Icon name="documents-outline" color={C.gold} /><Text style={s.heading}>Laporan tersimpan</Text></View><Text style={s.muted}>Laporan tetap tersedia selama aplikasi terbuka. Pilih laporan untuk membacanya tanpa generate ulang.</Text><Chips values={Object.keys(reports)} value={reports[outputTitle] ? outputTitle : Object.keys(reports)[0]} onChange={title => { setOutputTitle(title); setOutput(reports[title]); }} /></Card>}
     {output !== null && <Card><View style={s.row}><Icon name="document-text-outline" color={C.gold} /><Text style={s.heading}>{outputTitle}</Text></View><View style={s.divider} />{outputTitle === "Kalender ekonomi" && Array.isArray(output) ? <CalendarAgenda key={JSON.stringify(output)} events={output} onAnalyze={name => call("Event preview", "/calendar/preview", { query: name })} /> : <Result data={output} />}</Card>}
  </ScrollView>;
}

function Inbox({ connection, run, busy }: ScreenProps) {
  const [mode, setMode] = useState('Notifikasi'), [items, setItems] = useState<any[]>([]), [alerts, setAlerts] = useState<any[]>([]);
  const [subs, setSubs] = useState<Record<string, boolean>>({}), [symbol, setSymbol] = useState('XAUUSD'), [operator, setOperator] = useState('>='), [target, setTarget] = useState('');
  const refresh = async () => { const r = await Promise.all([request(connection, '/notifications'), request(connection, '/alerts'), request(connection, '/subscriptions')]); setItems(r[0]); setAlerts(r[1]); setSubs(r[2]); };
   useEffect(() => { void run(refresh); const listener = AppState.addEventListener('change', state => { if (state === 'active') void run(refresh); }); return () => listener.remove(); }, [connection]);
  return <ScrollView contentContainerStyle={s.page} refreshControl={<RefreshControl refreshing={busy} onRefresh={() => run(refresh)} tintColor={C.gold} />} keyboardShouldPersistTaps="handled"><Heading eyebrow="STAY IN THE LOOP" title="Sinyal yang perlu perhatian." /><Chips values={['Notifikasi', 'Price alert', 'Langganan']} value={mode} onChange={setMode} />
    {mode === 'Notifikasi' && <>{items.length > 0 && <Button title="Tandai semua dibaca" secondary disabled={busy} onPress={() => run(async () => { await request(connection, '/notifications/read', 'POST'); await refresh(); })} />}{!items.length && <Card><Empty title="Inbox masih tenang" detail="Alert dan laporan dari server akan muncul di sini. Tarik untuk memuat ulang." icon="notifications-outline" /></Card>}{items.map(item => <Card key={item.id}><View style={s.between}><Text style={[s.label, { color: item.read ? C.muted : C.gold }]}>{item.read ? 'DIBACA' : 'BARU'}</Text><Text style={[s.muted, { fontSize: 11 }]}>{new Date(item.created_at).toLocaleString('id-ID')}</Text></View><AIResponse text={item.body} /></Card>)}</>}
    {mode === 'Price alert' && <><Card><Field label="Pair" value={symbol} onChangeText={setSymbol} /><Chips values={['>', '>=', '<', '<=']} value={operator} onChange={setOperator} /><Field label="Harga target" value={target} onChangeText={setTarget} numeric /><Button title="Buat price alert" disabled={busy || !target} onPress={() => run(async () => { await request(connection, '/alerts', 'POST', { symbol, operator, target_price: Number(target) }); setTarget(''); await refresh(); })} /><Text style={s.muted}>Diperiksa sekitar setiap menit saat server aktif. Setelah terpenuhi, alert masuk inbox dan dihapus dari daftar aktif.</Text></Card>{alerts.map(a => <Card key={a.id}><View style={s.between}><Text style={s.heading}>{a.symbol} {a.operator} {a.target_price}</Text><Pressable accessibilityLabel={`Hapus alert ${a.id}`} disabled={busy} onPress={() => run(async () => { await request(connection, `/alerts/${a.id}`, 'DELETE'); await refresh(); })}><Icon name="trash-outline" color={C.red} /></Pressable></View></Card>)}</>}
    {mode === 'Langganan' && <Card>{[['macro', 'Macro briefing', '07:00, 14:00, 19:30 WIB'], ['session', 'Pengingat sesi', 'Asia, London, New York · jadwal bot'], ['debrief', 'Daily debrief', 'Setiap hari sekitar 05:00 WIB']].map(([key, label, detail]) => <View key={key} style={s.between}><View style={{ flex: 1 }}><Text style={s.heading}>{label}</Text><Text style={s.muted}>{detail}</Text></View><Switch accessibilityLabel={label} disabled={busy} value={!!subs[key]} trackColor={{ true: C.gold }} onValueChange={enabled => run(async () => setSubs(await request(connection, `/subscriptions/${key}`, 'PUT', { enabled })))} /></View>)}</Card>}
  </ScrollView>;
}

function Settings({ connection, run, busy, disconnect }: ScreenProps & { disconnect: () => Promise<void> }) {
  const [health, setHealth] = useState<any>(null), [notice, setNotice] = useState(''), [resetConfirm, setResetConfirm] = useState(false);
  useEffect(() => { void run(async () => setHealth(await request(connection, '/health'))); }, []);
  return <ScrollView contentContainerStyle={s.page}><Heading eyebrow="PRIVATE WORKSPACE" title="Pengaturan" /><Card><View style={s.between}><Text style={s.heading}>Koneksi server</Text><Icon name="lock-closed-outline" color={C.gold} /></View><Text selectable style={s.muted}>{connection.url}</Text>{health && <><Text style={[s.text, { color: C.gold }]}>Backend terhubung</Text><Text style={s.muted}>AI: {health.ai_configured ? 'Kunci tersedia; koneksi provider belum diuji' : 'Belum dikonfigurasi'}</Text><Text style={s.muted}>Teks: {health.text_model}{'\n'}Vision: {health.vision_model}</Text><Text style={s.muted}>Worker: {health.worker_enabled ? 'Diaktifkan' : 'Nonaktif'}{'\n'}Siklus terakhir: {health.worker_last_tick || 'Belum berjalan'}</Text></>}<Text style={s.muted}>Groq atau 9Router diatur melalui konfigurasi backend. Kunci AI tidak disimpan di aplikasi.</Text></Card>
    <Card><Text style={s.heading}>Notifikasi perangkat</Text><Text style={s.muted}>Aktifkan push untuk menerima pemberitahuan saat aplikasi ditutup. Memerlukan build fisik dan konfigurasi Expo/FCM/APNs.</Text><Button title="Aktifkan push di HP ini" disabled={busy} onPress={() => run(async () => { const token = await registerPush(connection); if (Platform.OS !== 'web') await SecureStore.setItemAsync('bayproject.push', token); setNotice('Perangkat terdaftar. Kirim notifikasi uji untuk memeriksa pengiriman.'); })} /><Button title="Kirim notifikasi uji" secondary disabled={busy} onPress={() => run(async () => { await request(connection, '/notifications/test', 'POST'); setNotice('Notifikasi tersimpan di inbox. Worker akan mencoba push ke perangkat terdaftar.'); })} /><Button title="Nonaktifkan push HP ini" secondary disabled={busy} onPress={() => run(async () => { const token = Platform.OS !== 'web' ? await SecureStore.getItemAsync('bayproject.push') : null; if (token) { await request(connection, '/devices', 'DELETE', { token }); await SecureStore.deleteItemAsync('bayproject.push'); } setNotice('Push perangkat ini dinonaktifkan.'); })} /></Card>
    {!!notice && <Card><Text style={[s.text, { color: C.gold }]}>{notice}</Text></Card>}
    <Card><Text style={s.heading}>Riwayat & akses</Text><Text style={s.muted}>Riwayat percakapan disimpan di server pribadi.</Text><Button title={resetConfirm ? 'Ya, hapus seluruh riwayat chat' : 'Reset percakapan AI'} secondary disabled={busy} onPress={() => { if (!resetConfirm) { setResetConfirm(true); return; } void run(async () => { await request(connection, '/chat', 'DELETE'); setResetConfirm(false); setNotice('Riwayat chat dihapus.'); }); }} />{resetConfirm && <Button title="Batal" secondary onPress={() => setResetConfirm(false)} />}<Button title="Keluar dari workspace" secondary disabled={busy} onPress={() => run(async () => { const token = Platform.OS !== 'web' ? await SecureStore.getItemAsync('bayproject.push') : null; if (token) { await request(connection, '/devices', 'DELETE', { token }); await SecureStore.deleteItemAsync('bayproject.push'); } await disconnect(); })} /></Card><Text style={[s.muted, { textAlign: 'center' }]}>Bayproject · Private edition 1.0.0</Text>
  </ScrollView>;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg }, frame: { flex: 1, width: '100%', maxWidth: 640, alignSelf: 'center' },
  header: { paddingHorizontal: 22, paddingVertical: 18, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: 1, borderColor: C.line },
  logo: { width: 58, height: 58, borderRadius: 10, backgroundColor: '#000' },
  brand: { fontSize: 19, color: C.gold, fontWeight: '700', letterSpacing: .4 }, brandSub: { fontSize: 7, color: C.silver, letterSpacing: 1.2, marginTop: 6 },
  iconButton: { padding: 11, backgroundColor: C.card, borderRadius: 14 }, dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: C.gold },
  error: { margin: 14, padding: 14, borderRadius: 14, backgroundColor: '#321e27', flexDirection: 'row', gap: 8, alignItems: 'center' },
  progress: { paddingVertical: 7, paddingHorizontal: 22, flexDirection: 'row', alignItems: 'center', gap: 10 },
  tabs: { flexDirection: 'row', borderTopWidth: 1, borderColor: C.line, paddingTop: 12, paddingBottom: 8, backgroundColor: C.bg },
  tab: { flex: 1, alignItems: 'center', paddingVertical: 3, gap: 5, minHeight: 52 }, tabDot: { width: 4, height: 4, borderRadius: 2, backgroundColor: C.gold },
  pairBadge: { backgroundColor: '#30291b', width: 45, height: 45, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  composer: { gap: 10, padding: 16, borderTopWidth: 1, borderColor: C.line, backgroundColor: C.card },
});
