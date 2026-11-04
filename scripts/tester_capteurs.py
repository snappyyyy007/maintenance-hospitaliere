#!/usr/bin/env python3
"""Vérifie le simulateur et la règle d'alerte, puis écrit les résultats attendus des requêtes KQL.

KQL ne s'exécute pas ici : `fuites_confirmees` reproduit la logique de fabric/requetes.kql
en Python, et data/capteurs/attendu_kql.csv sert à comparer les résultats dans Fabric.
Sans dépendance externe. Usage : python3 scripts/tester_capteurs.py
"""
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simuler_capteurs import DEBUT_DEMO, envoyer, generer  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = RACINE / "data" / "capteurs"
echecs = []


def verifier(condition, message):
    print(("OK    " if condition else "ÉCHEC ") + message)
    if not condition:
        echecs.append(message)


def instant(texte):
    return datetime.strptime(texte, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fuites_confirmees(evenements, dedoublonner=True):
    """Traduction ligne à ligne du bloc 2 de requetes.kql."""
    detections = [(e["capteur_id"], e["horodatage"]) for e in evenements if e["eau_detectee"]]
    lignes = sorted(set(detections) if dedoublonner else detections)
    resultats, precedent_confirme = [], False
    for rang, (capteur, horodatage) in enumerate(lignes):
        confirme = (
            rang >= 2 and lignes[rang - 2][0] == capteur
            and instant(horodatage) - instant(lignes[rang - 2][1]) <= timedelta(minutes=2)
        )
        meme_capteur = rang >= 1 and lignes[rang - 1][0] == capteur
        if confirme and not (meme_capteur and precedent_confirme):
            resultats.append((capteur, horodatage))
        precedent_confirme = confirme
    return resultats


def alertes_par_fenetre(evenements, debut, fin):
    """Rejoue le bloc 7 toutes les 5 minutes, avec seulement les messages déjà reçus."""
    alertes = []
    maintenant = debut + timedelta(minutes=5)
    while maintenant <= fin + timedelta(minutes=10):
        fin_fenetre = maintenant - timedelta(minutes=1)
        debut_fenetre = fin_fenetre - timedelta(minutes=5)
        recus = [e for e in evenements
                 if debut_fenetre - timedelta(minutes=15) < instant(e["horodatage"]) <= maintenant]
        for capteur, horodatage in fuites_confirmees(recus):
            if debut_fenetre < instant(horodatage) <= fin_fenetre:
                alertes.append((capteur, horodatage))
        maintenant += timedelta(minutes=5)
    return alertes


class LotFactice:
    def __init__(self, capacite):
        self.capacite, self.messages = capacite, []

    def add(self, message):
        if len(self.messages) >= self.capacite:
            raise ValueError("lot plein")
        self.messages.append(message)


class ProducteurFactice:
    def __init__(self, capacite):
        self.capacite, self.lots = capacite, []

    def create_batch(self):
        return LotFactice(self.capacite)

    def send_batch(self, lot):
        self.lots.append(lot.messages)


def tester_envoi(arrivees):
    print("\n=== Envoi ===")
    temps = [0.0]
    producteur = ProducteurFactice(capacite=7)
    envoyes = envoyer(arrivees, producteur, lambda texte: texte,
                      attendre=lambda secondes: temps.__setitem__(0, temps[0] + secondes),
                      horloge=lambda: temps[0])
    recus = [json.loads(m) for lot in producteur.lots for m in lot]
    verifier(envoyes == len(arrivees) == len(recus), f"{envoyes} événements envoyés, aucun perdu")
    verifier(recus == [e for _, e in arrivees], "l'ordre d'arrivée est conservé")
    verifier(max(len(lot) for lot in producteur.lots) <= 7, "les lots pleins sont découpés")
    verifier(all(producteur.lots), "aucun lot vide envoyé")
    verifier(abs(temps[0] - arrivees[-1][0]) < 1, f"cadence respectée ({temps[0] / 60:.1f} minutes simulées)")


def main():
    arrivees, verite_courte = generer(42, DEBUT_DEMO, 15)
    tester_envoi(arrivees)

    print("\n=== Scénario de démo ===")
    fichier = DOSSIER / "evenements_demo.jsonl"
    evenements = [json.loads(l) for l in fichier.read_text(encoding="utf-8").splitlines()]
    verite = json.loads((DOSSIER / "verite_demo.json").read_text(encoding="utf-8"))
    regeneres, _ = generer(42, DEBUT_DEMO, 60)
    verifier(evenements == [e for _, e in regeneres], f"{len(evenements)} événements, reproductibles avec la graine")

    colonnes_kql = re.findall(r"^\s+(\w+): \w+", (RACINE / "fabric" / "requetes.kql").read_text(encoding="utf-8"), re.M)
    verifier(colonnes_kql == list(evenements[0]), "les colonnes de la table KQL correspondent aux événements")
    ordre = [e["horodatage"] for e in evenements]
    verifier(ordre != sorted(ordre), "des événements arrivent dans le désordre")
    doublons = len(evenements) - len({(e["capteur_id"], e["horodatage"]) for e in evenements})
    verifier(doublons > 0, f"{doublons} doublons présents")

    print("\n=== Règle d'alerte ===")
    fuites = {f["capteur_id"]: f for f in verite["fuites"]}
    confirmees = fuites_confirmees(evenements)
    verifier({c for c, _ in confirmees} == set(fuites), f"les {len(fuites)} fuites sont confirmées, sans faux positif")
    verifier(len(confirmees) == len(fuites), "une seule confirmation par fuite")
    delais = [(instant(h) - instant(fuites[c]["debut"])).total_seconds() for c, h in confirmees]
    verifier(max(delais) <= 120, f"confirmation en {min(delais):.0f} à {max(delais):.0f} secondes")

    sans_dedoublonnage = {c for c, _ in fuites_confirmees(evenements, dedoublonner=False)}
    verifier(sans_dedoublonnage - set(fuites) != set(), "sans dédoublonnage, une fausse alarme passerait pour une fuite")

    debut, fin = instant(verite["debut"]), instant(verite["debut"]) + timedelta(minutes=verite["minutes"])
    alertes = alertes_par_fenetre(evenements, debut, fin)
    verifier(sorted(alertes) == sorted(confirmees), f"{len(alertes)} alertes : chaque fuite sort une seule fois")

    silencieux = max(instant(e["horodatage"]) for e in evenements) - timedelta(minutes=5)
    derniers = {}
    for evenement in evenements:
        derniers[evenement["capteur_id"]] = max(derniers.get(evenement["capteur_id"], ""), evenement["horodatage"])
    muets = {c for c, h in derniers.items() if instant(h) < silencieux}
    verifier(muets == {verite["capteur_silencieux"]}, f"capteur silencieux détecté : {sorted(muets)}")

    fausses = sorted({e["capteur_id"] for e in evenements if e["eau_detectee"]} - set(fuites))
    verifier(fausses == sorted(verite["fausses_alarmes"]), f"fausses alarmes : {fausses}")

    print("\n=== Durées ===")
    for minutes in (10, 15, 30):
        arrivees_courtes, verite_duree = generer(42, DEBUT_DEMO, minutes)
        trouvees = {c for c, _ in fuites_confirmees([e for _, e in arrivees_courtes])}
        verifier(trouvees == {f["capteur_id"] for f in verite_duree["fuites"]}, f"scénario de {minutes} minutes : 4 fuites confirmées")

    # À 150 minutes, une fuite dure 22 minutes, soit plus que le recul de 20 minutes de la requête d'alerte
    arrivees_longues, verite_longue = generer(42, DEBUT_DEMO, 150)
    longs = [e for _, e in arrivees_longues]
    alertes_longues = alertes_par_fenetre(longs, DEBUT_DEMO, DEBUT_DEMO + timedelta(minutes=150))
    verifier(sorted(c for c, _ in alertes_longues) == sorted(f["capteur_id"] for f in verite_longue["fuites"]),
             f"fuites de 22 minutes : {len(alertes_longues)} alertes, aucune répétée")

    locaux = {e["capteur_id"]: e for e in evenements}
    with open(DOSSIER / "attendu_kql.csv", "w", newline="", encoding="utf-8") as sortie:
        ecrivain = csv.writer(sortie)
        ecrivain.writerow(["requete", "heure", "capteur_id", "local_id", "detail"])
        for capteur, horodatage in sorted(confirmees, key=lambda c: c[1]):
            ecrivain.writerow(["2. Fuite confirmée", horodatage, capteur, locaux[capteur]["local_id"], locaux[capteur]["type_local"]])
        for capteur in fausses:
            nombre = len({e["horodatage"] for e in evenements if e["capteur_id"] == capteur and e["eau_detectee"]})
            ecrivain.writerow(["3. Fausse alarme", "", capteur, locaux[capteur]["local_id"], f"{nombre} détection(s)"])
        for capteur in sorted(muets):
            ecrivain.writerow(["4. Capteur silencieux", derniers[capteur], capteur, locaux[capteur]["local_id"], ""])
    print(f"\nEmpreinte du fichier de démo : {hashlib.sha256(fichier.read_bytes()).hexdigest()[:12]}")
    print(f"{len(echecs)} échec(s)")
    sys.exit(1 if echecs else 0)


if __name__ == "__main__":
    main()
