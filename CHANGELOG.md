# Cronologia di StellaTerm

## 0.5.0, 22 ottobre 2026

Prima versione nel repository proprio. StellaTerm, nato con Stella dentro la cartella Stuff fra il 20 e il 22 ottobre 2026 e arrivato lì a dichiararsi 2.0.0, riparte da qui con la 0.5.0, dopo una revisione completa del codice. Licenza GNU GPL 3.

Il terminale e lo schermo virtuale:

- Un programma che pulisce lo schermo con ESC [2J non cancella più tutto lo storico: le righe a schermo passano nello storico, come in Windows Terminal, e lo storico lo cancellano solo cls, clear e Ctrl+L. Prima bastava aprire un programma a schermo intero, o uno come cwapu che pulisce lo schermo, per perdere tutto l'output della sessione.
- All'uscita da un programma a schermo intero la sua ultima schermata resta nello storico dopo il comando che lo aveva aperto, non prima.
- ESC =, ESC >, ESC c, ESC ( 0 e simili non finiscono più stampati nel testo: con git log, che passa per less, comparivano un = e un > spuri.
- La tabulazione sposta il cursore senza cancellare ciò che scavalca.
- Le righe più lunghe di 200 colonne restano una riga sola nell'area di output, e gli spazi sull'ultima colonna non si perdono.
- Ideogrammi ed emoji occupano due colonne come nella console di Windows, e gli accenti staccati si uniscono alla loro lettera: le righe ridisegnate da cmd tornano al posto giusto.
- Una riga di 200 caratteri seguita da certe cancellazioni fermava per sempre la lettura dell'output; ora non succede più, e qualunque errore imprevisto del motore viene annotato in stellaterm_errori.log senza fermare il terminale.
- Le risposte alle domande dei programmi, posizione del cursore e attributi del terminale, arrivano nell'ordine giusto e con la posizione vera.
- Un titolo della finestra spezzato fra due letture non finisce più nel testo.
- Sequenze con conteggi enormi non bloccano più la finestra, e un output lungo si analizza da tre a dieci volte più in fretta.

La fine dei comandi:

- ping, timeout e i comandi che aspettano la rete non vengono più dati per finiti dopo tre decimi di secondo: un programma che tace su una riga vuota si considera in attesa solo dopo due secondi, e la fine vera arriva quando torna il prompt.
- Se un programma creduto in attesa stava solo dormendo, quando il prompt torna suona un segnale suo e il fuoco resta dov'è.
- La CPU dei processi si misura dal solo thread di lettura e al più ogni 250 millisecondi: prima un programma al lavoro poteva sembrare fermo, la misura consumava quasi metà di un processore e ogni Invio fermava la finestra per una decina di millisecondi.
- Righe di output come 45%, C# o OK> non vengono più scambiate per prompt; sono riconosciuti invece pdb, la continuazione di PowerShell, i venv con spazi nel nome e i prompt di cmd con l'ora.
- La lettura parte dalla prima riga vera della risposta anche quando un programma riscrive la riga del suo prompt, come fanno i prompt fra due ritorni carrello, e dopo un'interruzione parte da ciò che l'interruzione ha prodotto.
- Ctrl+C al prompt fermo non sposta più il fuoco su un output vecchio.

Il Tab:

- Nel Python di base e in pdb il Tab non si manda più, perché entrava nella riga come carattere invisibile e rovinava il comando successivo; lo stesso mentre un programma lavora o fuori da un prompt, dove svuotava il campo e mandava il testo al programma. In questi casi suona un segnale apposito.
- I completamenti lenti, come il primo Tab di PowerShell, si aspettano fino a sei decimi di secondo invece di essere dati per mancanti, e il giro del Tab non blocca più l'output per tutta la durata di un comando prolisso.
- Gli spazi finali della riga, per esempio dopo import, restano nel campo, e i comandi più lunghi di una riga si leggono interi.
- Per riscrivere la riga della shell StellaTerm usa il Backspace vero: il carattere di prima arrivava a cmd, a PowerShell e ai programmi come Ctrl+Backspace, che cancella parole intere. Anche Backspace premuto nell'area di output arriva ai programmi come Backspace.

Il campo di input e i suggerimenti:

- Invio esegue soltanto ciò che si è scritto, e la coda proposta si accetta con Freccia Destra o Fine. Prima il primo Invio accettava e il secondo eseguiva: un doppio Invio, o le ripetizioni che NVDA manda come pressioni nuove, eseguivano comandi mai scritti, e @st auto diventava @st auto_reset.
- Accettando un suggerimento il comando diventa la voce della cronologia con le sue maiuscole, e il conteggio sale solo quando il comando viene eseguito.
- I suggerimenti non compaiono più mentre si risponde a un programma o dentro Python, dove una risposta come y diventava yarn build.
- Ctrl+Freccia Destra e Sinistra scorrono i suggerimenti solo se uno è proposto, e altrimenti spostano per parole.
- La coda non accettata non resta più nel campo quando si esce con F6, Maiuscolo+Tab, Ctrl+Freccia Su o una lettera scritta dall'output.
- Ctrl+Freccia Su ripercorre i comandi in ordine di esecuzione anche fra una sessione e l'altra, e non più in ordine di frequenza.
- Con la cronologia piena i comandi nuovi non si scacciano più a vicenda: restano sempre gli ultimi 50 usati.
- Alla fine di un comando il fuoco non salta più nell'output se si sta già scrivendo il comando successivo.
- Ctrl+Invio e Alt+Invio non mettono più un a capo nel comando, Ctrl+Backspace non entra più nel prefisso, Backspace sulla riga dell'indirizzo non cancella più in silenzio l'ultimo carattere, e l'incolla dal menu contestuale passa dalle stesse regole di Ctrl+V, che ora toglie anche le tabulazioni. Se il prefisso viene rovinato per un'altra strada, si ricostruisce da solo.
- Ctrl+Maiuscolo+C copia la selezione, se c'è, come in Windows Terminal.
- cls e clear mandano cls solo al prompt di cmd o di PowerShell; in Python svuotano soltanto l'area di output, altrove vanno come sono.
- L'area di output si aggiorna solo nelle righe cambiate, e una sola volta per volta: chi la legge resta sulla stessa riga e non perde la selezione, anche quando lo storico pieno toglie righe in testa, e un output lungo non accumula più aggiornamenti arretrati.

Impostazioni e comandi @st:

- Un stellaterm_settings.json illeggibile non viene più sostituito in silenzio e poi sovrascritto alla chiusura: si mette da parte con la data nel nome, e StellaTerm lo dice. Il file si legge anche se è stato salvato con il BOM, e si scrive in modo atomico.
- Con due StellaTerm aperti, ognuno aggiunge al file i propri comandi e impostazioni senza cancellare quelli dell'altro.
- La cronologia si salva qualche secondo dopo ogni comando, non più solo alla chiusura.
- I messaggi dei comandi @st dicono salvato solo se il salvataggio è riuscito.
- @st buffer accetta da 50 a 100000 righe e dice quante righe ha tolto abbassando il limite.
- @st save scrive nella sottocartella salvataggi, esclusa da git, non sovrascrive mai un file esistente, rispetta le estensioni scritte e rifiuta i caratteri che Windows non ammette: i due punti finivano in un flusso nascosto del file.
- @st volume e i colori non si bloccano più con valori come inf.
- @st auto e @st set non hanno più righe di segni uguale.

Suoni e voce:

- Ogni azione ha un suono suo: prima Ctrl+L, Esc e @st auto_reset suonavano uguale, e un tasto mandato a un programma suonava come l'invio di un comando. Il Tab senza completamenti non usa più un'onda quadra. L'elenco delle azioni con i loro suoni sta in suoni_stellaterm.txt, per Acu_Maker, e i preset usati portano la firma di stellaterm nella collezione.
- Il primo suono non ferma più la finestra all'avvio: audio e voce si preparano in sottofondo, e il fuoco arriva subito nel campo di input.
- Gli annunci vanno a NVDA prima che a JAWS, sempre nello stesso ordine.
- Chiudendo con un programma ancora in esecuzione, una domanda chiede conferma; alla chiusura StellaTerm non scrive più exit, che arrivava al programma in primo piano.
- Se pywinpty manca o la console non parte, una finestra lo dice invece di chiudere in silenzio.

Il progetto:

- Il manuale è README.md, mostrato su GitHub e con F1 senza i segni del Markdown, riscritto con frasi intere e senza separatori.
- GBUtils si importa dalla PYTHONPATH come negli altri programmi, senza percorsi scritti per esteso, e i percorsi passano da cartella_applicazione e percorso_risorsa.
- Prove con pytest: il motore su flussi sintetici e con cmd.exe vero, impostazioni, suoni, e le finestre su un desktop di Windows nascosto.
