#!/usr/bin/env python3
"""Simule des capteurs de fuite d'eau FICTIFS, vers un fichier ou un Eventstream Fabric.

Le scénario est fixe pour une graine donnée : 3 fuites, dont une qui se propage à l'étage
du dessous, des fausses alarmes, un capteur qui se tait, des doublons et des messages perdus.

Fichier de démo (sans dépendance) :
    python3 scripts/simuler_capteurs.py --demo

Envoi en direct (azure-eventhub requis, 1 lecture toutes les 30 s par capteur) :
    export EVENTSTREAM_CONNEXION="..."
    python scripts/simuler_capteurs.py --minutes 15
"""
import argparse
import csv
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DEBUT_DEMO = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)
NB_CAPTEURS = 40
INTERVALLE_S = 30
TAUX_PERTE = 0.015
TAUX_DOUBLON = 0.01


def lire_locaux():
    with open(RACINE / "data" / "brut" / "locaux.csv", encoding="utf-8", newline="") as fichier:
        locaux = list(csv.DictReader(fichier))
    for local in locaux:
        local["etage"], local["criticite"] = int(local["etage"]), int(local["criticite"])
    return sorted(locaux, key=lambda l: l["local_id"])


def choisir_capteurs(rng, locaux):
    """Place les capteurs ; les rôles du scénario sont choisis en premier."""
    heritage = [l for l in locaux if l["pavillon_id"] == "PAV-B"]
    etages = {l["etage"] for l in heritage}
    etage = rng.choice(sorted(e for e in etages if e - 1 in etages))
    roles = {
        "fuite_origine": rng.choice([l for l in heritage if l["etage"] == etage]),
        "fuite_cascade": rng.choice([l for l in heritage if l["etage"] == etage - 1]),
        "fuite_critique": rng.choice([l for l in locaux if l["pavillon_id"] == "PAV-C" and l["criticite"] == 3]),
        "fuite_simple": rng.choice([l for l in locaux if l["pavillon_id"] == "PAV-F"]),
    }
    choisis = {l["local_id"]: l for l in roles.values()}
    autres = [l for l in locaux if l["local_id"] not in choisis]
    for local in rng.sample(autres, NB_CAPTEURS - len(choisis)):
        choisis[local["local_id"]] = local

    capteurs = []
    for rang, local in enumerate(sorted(choisis.values(), key=lambda l: l["local_id"]), start=1):
        capteurs.append({
            "capteur_id": f"CAP-{rang:03d}", "pavillon_id": local["pavillon_id"], "local_id": local["local_id"],
            "etage": local["etage"], "type_local": local["type_local"], "criticite": local["criticite"],
            "humidite_base": rng.uniform(36, 48), "temperature_base": rng.uniform(19, 24),
            "batterie": rng.uniform(40, 100),
        })
    par_local = {c["local_id"]: c for c in capteurs}
    return capteurs, {role: par_local[local["local_id"]] for role, local in roles.items()}


def creer_scenario(rng, capteurs, roles, duree_s):
    """Les moments sont proportionnels à la durée, pour garder le même récit à 15 ou à 60 minutes."""
    def fuite(role, debut, origine=None):
        longueur = max(240, duree_s * 0.15)
        return {"capteur": roles[role], "debut": debut, "fin": debut + longueur, "origine": origine}

    debut_origine = duree_s * 0.20
    fuites = [
        fuite("fuite_origine", debut_origine),
        fuite("fuite_cascade", debut_origine + max(90, duree_s * 0.10), origine=roles["fuite_origine"]["capteur_id"]),
        fuite("fuite_critique", duree_s * 0.45),
        fuite("fuite_simple", duree_s * 0.70),
    ]

    speciaux = {f["capteur"]["capteur_id"] for f in fuites}
    calmes = [c for c in capteurs if c["capteur_id"] not in speciaux]
    bavard, double, simple1, simple2, muet = rng.sample(calmes, 5)
    instant = lambda fraction: int(duree_s * fraction / INTERVALLE_S) * INTERVALLE_S
    fausses_alarmes = [
        {"capteur": simple1, "ticks": [instant(0.15)], "repetitions": 1},
        {"capteur": simple2, "ticks": [instant(0.35)], "repetitions": 1},
        {"capteur": double, "ticks": [instant(0.55), instant(0.55) + INTERVALLE_S], "repetitions": 1},
        # Une seule lecture reçue 3 fois : sans dédoublonnage, elle ressemblerait à une fuite
        {"capteur": bavard, "ticks": [instant(0.80)], "repetitions": 3},
    ]
    muet["batterie"] = 3.0
    return fuites, fausses_alarmes, {"capteur": muet, "arret": duree_s * 0.60}


def humidite(rng, capteur, tick, fuites):
    valeur = capteur["humidite_base"] + rng.gauss(0, 1.2)
    for fuite in fuites:
        if fuite["capteur"] is capteur and tick >= fuite["debut"]:
            montee = 50 * (1 - math.exp(-(min(tick, fuite["fin"]) - fuite["debut"]) / 120))
            sechage = math.exp(-max(0, tick - fuite["fin"]) / 300)
            valeur += montee * sechage
    return round(min(valeur, 99.0), 1)


def generer(graine, debut, minutes):
    """Renvoie les événements dans leur ordre d'arrivée, qui n'est pas tout à fait l'ordre chronologique."""
    rng = random.Random(graine)
    capteurs, roles = choisir_capteurs(rng, lire_locaux())
    duree_s = minutes * 60
    fuites, fausses_alarmes, silence = creer_scenario(rng, capteurs, roles, duree_s)
    ticks_fausses = {
        (a["capteur"]["capteur_id"], tick): a["repetitions"] for a in fausses_alarmes for tick in a["ticks"]
    }

    arrivees = []
    for tick in range(0, duree_s, INTERVALLE_S):
        for capteur in capteurs:
            if capteur is silence["capteur"] and tick >= silence["arret"]:
                continue
            cle = (capteur["capteur_id"], tick)
            # Une fausse alarme est toujours reçue, pour que le scénario reste certain
            if cle not in ticks_fausses and rng.random() < TAUX_PERTE:
                continue
            en_fuite = any(f["capteur"] is capteur and f["debut"] <= tick < f["fin"] for f in fuites)
            secondes = tick + rng.randint(0, 9)
            evenement = {
                "capteur_id": capteur["capteur_id"],
                "horodatage": (debut + timedelta(seconds=secondes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "pavillon_id": capteur["pavillon_id"], "local_id": capteur["local_id"], "etage": capteur["etage"],
                "type_local": capteur["type_local"], "criticite": capteur["criticite"],
                "humidite_pct": humidite(rng, capteur, tick, fuites),
                "temperature_c": round(capteur["temperature_base"] + rng.gauss(0, 0.2), 1),
                "eau_detectee": en_fuite or cle in ticks_fausses,
                "batterie_pct": round(capteur["batterie"] - tick / 3600 * 0.1, 1),
            }
            copies = ticks_fausses.get(cle, 2 if rng.random() < TAUX_DOUBLON else 1)
            for _ in range(copies):
                arrivees.append((secondes + rng.uniform(0, 20), evenement))
    arrivees.sort(key=lambda a: a[0])

    def instant(secondes):
        return (debut + timedelta(seconds=secondes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    verite = {
        "graine": graine, "debut": instant(0), "minutes": minutes, "nb_capteurs": len(capteurs),
        "fuites": [
            {"capteur_id": f["capteur"]["capteur_id"], "local_id": f["capteur"]["local_id"],
             "debut": instant(f["debut"]), "fin": instant(f["fin"]), "origine": f["origine"]}
            for f in fuites
        ],
        "fausses_alarmes": [a["capteur"]["capteur_id"] for a in fausses_alarmes],
        "capteur_silencieux": silence["capteur"]["capteur_id"],
    }
    return [(decalage, evenement) for decalage, evenement in arrivees], verite


def ecrire_demo(graine):
    arrivees, verite = generer(graine, DEBUT_DEMO, 60)
    dossier = RACINE / "data" / "capteurs"
    dossier.mkdir(parents=True, exist_ok=True)
    with open(dossier / "evenements_demo.jsonl", "w", encoding="utf-8") as fichier:
        for _, evenement in arrivees:
            fichier.write(json.dumps(evenement, ensure_ascii=False) + "\n")
    verite["nb_evenements"] = len(arrivees)
    (dossier / "verite_demo.json").write_text(json.dumps(verite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(arrivees)} événements -> {dossier / 'evenements_demo.jsonl'}")


def envoyer(arrivees, producteur, vers_message, attendre=time.sleep, horloge=time.monotonic):
    """Envoie chaque événement à son moment, en lots qui respectent la taille maximale de l'Eventstream."""
    depart = horloge()
    envoyes = 0
    rang = 0
    while rang < len(arrivees):
        pause = arrivees[rang][0] - (horloge() - depart)
        if pause > 0:
            attendre(pause)
        ecoule = horloge() - depart
        lot = producteur.create_batch()
        taille = 0
        while rang < len(arrivees) and arrivees[rang][0] <= ecoule:
            message = vers_message(json.dumps(arrivees[rang][1], ensure_ascii=False))
            try:
                lot.add(message)
            except ValueError:  # lot plein : on l'envoie et on repart avec un lot vide
                producteur.send_batch(lot)
                envoyes += taille
                lot, taille = producteur.create_batch(), 0
                lot.add(message)
            taille += 1
            rang += 1
        if taille:
            producteur.send_batch(lot)
            envoyes += taille
    return envoyes


def envoyer_en_direct(graine, minutes):
    connexion = os.environ.get("EVENTSTREAM_CONNEXION")
    if not connexion:
        sys.exit("Définis EVENTSTREAM_CONNEXION avec la chaîne de connexion de l'Eventstream.")
    from azure.eventhub import EventData, EventHubProducerClient

    arrivees, verite = generer(graine, datetime.now(timezone.utc), minutes)
    print(f"{len(arrivees)} événements sur {minutes} minutes. Fuites prévues :")
    for fuite in verite["fuites"]:
        print(f"  {fuite['capteur_id']} ({fuite['local_id']}) à {fuite['debut']}")
    producteur = EventHubProducerClient.from_connection_string(connexion)
    with producteur:
        print(f"{envoyer(arrivees, producteur, EventData)} événements envoyés")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true", help="écrit le scénario de 60 minutes dans data/capteurs")
    parser.add_argument("--minutes", type=int, default=15, help="durée de l'envoi en direct")
    parser.add_argument("--graine", type=int, default=42)
    args = parser.parse_args()
    if args.demo:
        ecrire_demo(args.graine)
    else:
        envoyer_en_direct(args.graine, args.minutes)


if __name__ == "__main__":
    main()
