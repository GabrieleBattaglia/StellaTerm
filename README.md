# StellaTerm

StellaTerm è un terminale accessibile per Windows, scritto in wxPython e pensato per chi lavora con un lettore di schermo, NVDA o JAWS, e con un display braille. Dentro gira il Prompt dei comandi vero, cmd.exe, attraverso la pseudo console di Windows: tutto ciò che funziona in cmd funziona anche qui, compresi PowerShell, Python, IPython e ssh lanciati da lì.

Versione 0.5.0 del 22 ottobre 2026. Autori: Gabriele Battaglia (IZ4APU), Stella (Gemini 4 Argon) e ClaudIA (Claude Opus 5.5, UltraCode). Il programma è software libero, distribuito con la licenza GNU GPL versione 3.

## 1. Che cosa serve

- Windows 10 versione 1809 o successiva, oppure Windows 11, perché StellaTerm usa la pseudo console di Windows, ConPTY.
- Python 3.12 o più recente; StellaTerm è sviluppato con Python 3.14.7.
- Le librerie elencate in requirements.txt, che si installano con `python -m pip install -r requirements.txt`: wxPython per la finestra, pywinpty per la pseudo console, numpy, scipy e sounddevice per gli effetti sonori, accessible-output2 per parlare con NVDA e JAWS.
- GBUtils, la libreria comune del parco software di Gabriele Battaglia, che non sta su PyPI: si clona da https://github.com/GabrieleBattaglia/GBUtils e la sua cartella va nella variabile d'ambiente PYTHONPATH.

StellaTerm si avvia dalla sua cartella con `python stellaterm.py`. Se pywinpty manca o la console non parte, una finestra lo dice e spiega come rimediare.

## 2. La finestra

All'avvio la finestra di StellaTerm si apre sempre massimizzata ed è divisa in due aree di testo, che occupano entrambe tutta la larghezza dello schermo.

- L'area superiore, Output del terminale, è in sola lettura e occupa la maggior parte dello schermo in verticale. Contiene lo storico dell'output senza righe vuote, così si esplora riga per riga con le frecce senza incontrare buchi di silenzio. Mentre arriva output nuovo l'area cambia solo nelle righe nuove o diverse: chi la sta leggendo resta sulla stessa riga e non perde la selezione.
- L'area inferiore, Area di input, occupa la fascia in basso ed è dedicata alla scrittura dei comandi e alla lettura del percorso o del prompt attivo.

## 3. L'area di input

L'area di input mostra sempre due righe di riferimento. Sulla prima c'è l'indirizzo o il prompt della sessione, per esempio E:\dati\git> oppure E:\dati\git> >>> quando si è dentro Python. Sulla seconda c'è il numero progressivo del comando fra parentesi quadre, seguito da uno spazio, per esempio [14], e dopo di lui si scrive il comando.

Il cursore di scrittura si posiziona da solo dopo [14], ma l'intera area si esplora con le frecce come una normale casella di testo su più righe: Freccia Su porta sulla riga dell'indirizzo, Freccia Giù riporta sul comando. Se si comincia a scrivere mentre il cursore è sulla riga dell'indirizzo o dentro [14], StellaTerm lo riporta dopo [14] senza toccare né l'indirizzo né il numero. Backspace e Canc non cancellano mai l'indirizzo o il numero: con il cursore lì sopra non fanno niente. Se un'altra strada, per esempio Annulla o il menu contestuale, rovinasse le due righe di riferimento, StellaTerm le ricostruisce subito conservando il comando.

Quando si incolla, con Ctrl+V, Maiuscolo+Ins o dal menu contestuale, il testo va sempre nella zona del comando: gli a capo e le tabulazioni diventano spazi, e gli altri caratteri di controllo si tolgono, perché alla shell arriverebbero come tasti.

## 4. Suggerimenti e cronologia

Mentre si scrive un comando, StellaTerm propone di seguito il comando già usato più spesso che comincia con le stesse lettere: la parte scritta resta com'è e la coda proposta viene aggiunta e selezionata, così NVDA la legge subito. Continuando a scrivere, la proposta si restringe ai comandi compatibili.

- Freccia Destra o Fine accettano la coda proposta: il comando diventa quello della cronologia, maiuscole comprese, e il cursore va in fondo per aggiungere altri parametri.
- Invio esegue soltanto ciò che si è scritto: la coda non accettata si scarta. Così un doppio Invio, o le ripetizioni di Invio che NVDA manda come pressioni nuove, non eseguono mai un comando che non si è scritto.
- Ctrl+Freccia Destra e Ctrl+Freccia Sinistra scorrono avanti e indietro fra i suggerimenti compatibili, quando una coda è proposta; altrimenti spostano il cursore per parole, come sempre.
- Backspace, Canc ed Esc tolgono soltanto la coda proposta e lasciano intatto ciò che si è scritto. Qualunque altro spostamento, per esempio Freccia Sinistra o Ctrl+Freccia Su, scarta la coda prima di muoversi.

I suggerimenti vengono dalla cronologia della shell, quindi compaiono al prompt di cmd, di PowerShell o di una shell remota, ma non mentre si risponde a un programma e non dentro un interprete come Python. I comandi @st si propongono sempre.

La cronologia conserva fino a 1000 comandi, ciascuno con il numero di volte in cui è stato eseguito: il conteggio sale solo quando il comando viene eseguito davvero, non quando lo si accetta. Quando la cronologia è piena escono i comandi usati meno spesso, e a parità quelli usati meno di recente, ma mai gli ultimi 50 usati, così anche i comandi nuovi hanno il tempo di essere imparati. Si contano i comandi dati alla shell e i comandi @st; le risposte ai programmi e le righe scritte dentro un interprete no.

Ctrl+Freccia Su e Ctrl+Freccia Giù scorrono la cronologia dei comandi in ordine di esecuzione, dal più recente all'indietro, anche fra una sessione e l'altra; le frecce semplici restano libere per esplorare le righe del campo. La cronologia si salva qualche secondo dopo ogni comando e alla chiusura. Con due StellaTerm aperti insieme, ognuno aggiunge al file i propri comandi e le proprie impostazioni senza cancellare quelli dell'altro.

## 5. Tasti

- Invio, nel campo di input: esegue il comando, con il suono di invio, mandandolo alla sessione. Ctrl+Invio e Alt+Invio non fanno niente, invece di mettere un a capo dentro il comando.
- Tab, nel campo di input: chiede alla shell o all'interprete il suo completamento, per esempio dei nomi di file e cartelle in cmd e in PowerShell, o di attributi e metodi in IPython. Un completamento unico riempie il campo con un suono; più completamenti fanno sentire un altro suono e leggere le prime scelte; nessun completamento ha un suono suo. Il Tab non è disponibile, e lo dice un suono apposito, mentre un programma sta lavorando, fuori da un prompt riconosciuto e nel prompt >>> del Python di base e in pdb, dove il Tab entrerebbe nella riga come carattere invisibile.
- Maiuscolo+Tab oppure F6: spostano il fuoco fra il campo di input e l'area di output. Nell'area di output anche il solo Tab riporta giù al campo di input.
- Esc, nel campo di input: toglie la coda di un suggerimento, oppure svuota il comando scritto dopo [n], lasciando intatti l'indirizzo e il numero.
- Esc, nell'area di output: riporta il fuoco nel campo di input; se un programma sta aspettando un tasto, Esc va a lui.
- Lettere, numeri e simboli scritti nell'area di output: al prompt della shell o di un interprete il fuoco scende nel campo di input con il carattere già scritto. Quando invece un programma interattivo aspetta tasti singoli, per esempio con key(), menu() o manuale() di GBUtils, come in gabryscola, i tasti premuti nell'area di output vanno direttamente a lui, anche Spazio, Invio, Esc e Backspace, e si resta nell'area di output a leggere la risposta. Se il programma chiede una riga intera, per esempio con input() o dgt(), conviene scendere nel campo di input con Tab o F6.
- Backspace, nell'area di output al prompt: scende nel campo di input e cancella l'ultimo carattere del comando.
- Ctrl+V, nell'area di output: scende nel campo di input e incolla nella zona del comando.
- Ctrl+Backspace, nel campo di input: cancella la parola prima del cursore, senza mai entrare nell'indirizzo o nel numero.
- Ctrl+C: se c'è del testo selezionato lo copia negli appunti; altrimenti manda l'interruzione, Ctrl+C, al programma in esecuzione. La coda di un suggerimento non conta come selezione. Al prompt fermo l'interruzione non sposta il fuoco.
- Ctrl+Maiuscolo+C: come in Windows Terminal, copia la selezione se c'è; altrimenti manda l'interruzione.
- Ctrl+Pausa: manda sempre l'interruzione.
- Ctrl+L: svuota l'area di output.
- F1: mostra questo manuale nell'area di output.

Alla fine di un comando suona il segnale di fine e il fuoco passa nell'area di output, con il cursore all'inizio della risposta. Se però si sta già scrivendo il comando successivo nel campo di input, il fuoco resta lì: suona soltanto il segnale, e il cursore dell'output è già all'inizio della risposta, pronto per F6 o Maiuscolo+Tab. Dopo un'interruzione la lettura parte da ciò che l'interruzione ha prodotto, per esempio le statistiche di ping o il messaggio di Python.

Scrivere cls o clear al prompt di cmd o di PowerShell svuota l'area di output e anche lo schermo della shell. Dentro Python o IPython svuota soltanto l'area di output, senza mandare niente all'interprete, che risponderebbe con un errore. Altrove, per esempio in una shell remota via ssh o in risposta a un programma, la parola va così com'è.

Chiudendo StellaTerm mentre nel terminale gira ancora un programma, una domanda dice quale e chiede se chiudere comunque, perché la chiusura lo terminerebbe; la risposta predefinita è No.

## 6. I comandi interni @st

Tutto ciò che comincia con @st seguito da uno spazio, oppure @st da solo, è un comando di StellaTerm e non arriva alla shell; @sthelp, scritto attaccato, va invece alla shell come qualunque altro comando. Le impostazioni cambiate con i comandi @st sono permanenti: si salvano nel file stellaterm_settings.json, nella cartella del programma, e si applicano subito e a ogni avvio. Un comando di impostazione scritto senza valore mostra il valore attuale e come si usa. Se il file non si può scrivere, il messaggio dice che l'impostazione vale per questa sessione ma non è stata salvata, e perché.

1. `@st help` mostra questo manuale nell'area di output, con il cursore all'inizio del testo. Esempio: `@st help`.
2. `@st quit` chiude StellaTerm; `@st exit` fa lo stesso. Esempio: `@st quit`.
3. `@st buffer n` imposta a n il numero massimo di righe conservate nell'area di output, da 50 a 100000. Abbassandolo, le righe più vecchie oltre il nuovo limite si tolgono subito, e il messaggio dice quante. Esempio: `@st buffer 8000`.
4. `@st save` salva tutto il contenuto dell'area di output in un file di testo nella sottocartella salvataggi della cartella del programma, con data e ora nel nome, per esempio stellaterm_buffer_2026-10-22_14-30-00.txt. Si può indicare un nome: senza estensione gli si aggiungono data, ora e .txt, con un'estensione la si rispetta, e un percorso completo si rispetta anche lui. Un file che esiste già non viene mai sovrascritto: al nome si aggiunge _2, _3 e così via. I caratteri che Windows non ammette nei nomi dei file, come i due punti o il punto interrogativo, vengono rifiutati con un messaggio. Esempi: `@st save` e `@st save prova`, che scrive prova seguito da data e ora, con l'estensione .txt.
5. `@st rgb_i_b r g b` imposta il colore di sfondo del campo di input con tre percentuali da 0 a 100 per rosso, verde e blu. Esempio: `@st rgb_i_b 0 20 0`.
6. `@st rgb_i_f r g b` imposta allo stesso modo il colore dei caratteri del campo di input. Esempio: `@st rgb_i_f 100 100 0`.
7. `@st rgb_o_b r g b` imposta il colore di sfondo dell'area di output. Esempio: `@st rgb_o_b 5 5 10`.
8. `@st rgb_o_f r g b` imposta il colore dei caratteri dell'area di output. Esempio: `@st rgb_o_f 90 100 90`.
9. `@st size_i n` imposta la dimensione in punti dei caratteri del campo di input, da 6 a 72. Esempio: `@st size_i 14`.
10. `@st size_o n` imposta la dimensione in punti dei caratteri dell'area di output, da 6 a 72. Esempio: `@st size_o 16`.
11. `@st set` mostra tutti i parametri di StellaTerm con i loro valori attuali e il percorso del file delle impostazioni. Esempio: `@st set`.
12. `@st volume n%` imposta il volume degli effetti sonori, da 0 a 100; il simbolo % è facoltativo, e 0 spegne tutti i suoni. Esempio: `@st volume 70%`.
13. `@st auto` mostra la tabella di tutti i comandi della cronologia con il numero di volte fra parentesi, dal più usato al meno usato, a tutta larghezza e in colonne allineate. Esempio: `@st auto`.
14. `@st auto_reset` cancella tutti i comandi della cronologia, con i loro conteggi, sia dai suggerimenti sia da Ctrl+Freccia Su, e salva subito, senza chiedere conferma. Esempio: `@st auto_reset`.

## 7. Lo schermo virtuale e le applicazioni testuali

Il motore di StellaTerm tiene in memoria una griglia di 50 righe per 200 colonne con il suo storico, e vi compone tutte le sequenze di movimento del cursore che i programmi mandano: tabelle, riquadri, cruscotti e menu, come quelli dei suggerimenti di IPython, finiscono nella loro geometria corretta, e poi si leggono riga per riga senza righe vuote.

- Le righe più lunghe di 200 colonne, che lo schermo manda a capo, nell'area di output restano una riga sola.
- Gli ideogrammi e le emoji occupano due colonne, come nella console di Windows, e gli accenti staccati si uniscono alla loro lettera.
- Un programma che pulisce lo schermo, per esempio con la sequenza ESC [2J, non si porta via lo storico: le righe che erano a schermo passano prima nello storico, come in Windows Terminal. Lo storico lo cancellano soltanto cls, clear e Ctrl+L.
- I programmi a schermo intero, come editor, pager e menu, lavorano in uno schermo a parte; quando escono, la loro ultima schermata resta nello storico, dopo il comando che li aveva aperti.

## 8. Quando un comando è finito

StellaTerm considera finito un comando quando la shell torna al suo prompt e non ha programmi che lavorano. Considera in attesa un programma che tace senza usare il processore: dopo tre decimi di secondo se il cursore sta dopo una domanda, come Nome: di un input(), e dopo due secondi se il cursore è su una riga vuota, come fra una risposta e l'altra di ping. In entrambi i casi suona il segnale di fine e il fuoco va all'inizio della risposta. Se un programma creduto in attesa stava invece solo dormendo, quando il prompt torna davvero suona un secondo segnale, diverso, e il fuoco resta dov'è.

## 9. I suoni

Ogni azione di StellaTerm ha un suo effetto sonoro, preso dalla collezione comune Acu_Collection.json di GBUtils, e due azioni diverse non hanno mai lo stesso suono. L'elenco completo, un'azione per riga con il nome del suono, sta nel file suoni_stellaterm.txt: con quello in mano i suoni si ritoccano con Acu_Maker. Il volume di tutti gli effetti si regola con @st volume.

## 10. I file che StellaTerm scrive

Tutti nella cartella del programma, e tutti esclusi dal repository git:

- stellaterm_settings.json, con le impostazioni e la cronologia dei comandi. Se all'avvio non si legge, per esempio perché è stato ritoccato a mano con un errore, StellaTerm lo mette da parte come stellaterm_settings.json.illeggibile seguito da data e ora, lo dice nell'area di output e riparte dai valori predefiniti, senza sovrascriverlo.
- La sottocartella salvataggi, con i buffer salvati da @st save.
- stellaterm_errori.log, solo se StellaTerm incontra un errore interno: lo annota lì e continua a lavorare, e la prima volta lo dice nell'area di output.

## 11. Licenza

StellaTerm è distribuito con la licenza GNU General Public License versione 3, il cui testo integrale sta nel file LICENSE.
