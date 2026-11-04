#!/usr/bin/env python3
"""Vérifie que le jeu de données contient bien les tendances et les défauts annoncés.

Le script fait un nettoyage minimal, puis échoue (code 1) si une attente n'est pas respectée.
Usage : python3 scripts/valider_donnees.py
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent / "data"
CANONIQUES = {
    "plomberie": "Plomberie", "plomb.": "Plomberie",
    "électricité": "Électricité", "electricite": "Électricité", "elec": "Électricité",
    "cvc": "CVC", "hvac": "CVC", "c.v.c.": "CVC",
    "dégât d'eau": "Dégât d'eau", "degat d'eau": "Dégât d'eau",
    "dégâts d'eau": "Dégât d'eau", "degat eau": "Dégât d'eau",
}
echecs = []


def verifier(condition, message):
    print(("OK    " if condition else "ÉCHEC ") + message)
    if not condition:
        echecs.append(message)


def lire_date(texte):
    if not texte:
        return None
    for gabarit in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(texte, gabarit)
        except ValueError:
            pass
    raise ValueError(f"date illisible : {texte!r}")


def lire(nom):
    with open(RACINE / "brut" / nom, encoding="utf-8", newline="") as fichier:
        return list(csv.DictReader(fichier))


def nombre(texte):
    return float(texte) if texte else 0.0


def main():
    brut = lire("bons_de_travail.csv")
    manifeste = json.loads((RACINE / "manifeste.json").read_text(encoding="utf-8"))
    defauts = manifeste["defauts_injectes"]

    # Unicité et défauts du manifeste
    uniques = {tuple(l.items()): l for l in brut}
    bons = list(uniques.values())
    ids = Counter(l["bon_id"] for l in bons)
    verifier(len(brut) == manifeste["lignes_brutes"], f"{len(brut)} lignes brutes, comme le manifeste")
    verifier(len(bons) == manifeste["bons_uniques"], f"{len(bons)} bons après retrait des doublons exacts")
    verifier(max(ids.values()) == 1, "bon_id unique après retrait des doublons")
    verifier(len(brut) - len(bons) == defauts["doublons_exacts"], "nombre de doublons exacts conforme")

    variantes = sum(1 for l in bons if l["categorie"] not in set(CANONIQUES.values()))
    verifier(abs(variantes - defauts["categories_mal_ecrites"]) <= 3, f"{variantes} catégories à normaliser")
    for bon in bons:
        bon["categorie"] = CANONIQUES.get(bon["categorie"].strip().lower(), bon["categorie"])
        bon["pavillon_id"] = bon["pavillon_id"].strip().upper().replace(" ", "-")
        bon["creation"] = lire_date(bon["date_creation"])
        bon["fermeture"] = lire_date(bon["date_fermeture"])
    verifier(set(b["categorie"] for b in bons) == set(CANONIQUES.values()), "4 catégories après normalisation")

    pavillons = {p["pavillon_id"] for p in lire("pavillons.csv")}
    locaux = {l["local_id"] for l in lire("locaux.csv")}
    techniciens = {t["technicien_id"] for t in lire("techniciens.csv")}
    verifier(all(b["pavillon_id"] in pavillons for b in bons), "tous les pavillons existent")
    verifier(all(b["local_id"] in locaux for b in bons), "tous les locaux existent")
    verifier(all(b["technicien_id"] in techniciens for b in bons if b["technicien_id"]), "tous les techniciens existent")
    ids_bons = set(ids)
    verifier(all(b["bon_parent_id"] in ids_bons for b in bons if b["bon_parent_id"]), "tous les bons parents existent")

    fermes = [b for b in bons if b["statut"] == "Fermé"]
    inverses = [b for b in fermes if b["fermeture"] < b["creation"]]
    verifier(len(inverses) == defauts["fermetures_avant_creation"], f"{len(inverses)} fermetures avant création")
    negatifs = [b for b in fermes if nombre(b["cout_pieces"]) < 0]
    verifier(len(negatifs) == defauts["couts_negatifs"], f"{len(negatifs)} coûts négatifs")

    # Tendances métier
    valides = [b for b in fermes if b["fermeture"] >= b["creation"] and nombre(b["cout_pieces"]) >= 0]
    superficie = {p["pavillon_id"]: float(p["superficie_m2"]) for p in lire("pavillons.csv")}
    eau = Counter(b["pavillon_id"] for b in bons if b["categorie"] == "Dégât d'eau")
    densite = {p: eau[p] / superficie[p] for p in superficie}
    print("\nDégâts d'eau par 10 000 m² :", {p: round(v * 1e4, 1) for p, v in sorted(densite.items())})
    verifier(max(densite, key=densite.get) == "PAV-B", "le pavillon Héritage a la plus forte densité de dégâts d'eau")

    par_mois = Counter(b["creation"].month for b in bons if b["categorie"] == "CVC")
    verifier(par_mois[7] > par_mois[4] * 1.5, f"CVC : juillet ({par_mois[7]}) nettement au-dessus d'avril ({par_mois[4]})")
    eau_mois = Counter(b["creation"].month for b in bons if b["categorie"] == "Dégât d'eau")
    verifier(eau_mois[4] > eau_mois[9] * 2, f"dégâts d'eau : avril ({eau_mois[4]}) au-dessus de septembre ({eau_mois[9]})")

    cibles = {p["priorite"]: float(p["cible_fermeture_heures"]) for p in lire("priorites.csv")}
    delais = defaultdict(list)
    respect = defaultdict(list)
    for bon in valides:
        heures = (bon["fermeture"] - bon["creation"]).total_seconds() / 3600
        delais[bon["priorite"]].append(heures)
        respect[bon["priorite"]].append(heures <= cibles[bon["priorite"]])
    print("\nPriorité : médiane en heures, respect de la cible")
    for priorite in sorted(delais):
        serie = sorted(delais[priorite])
        taux = sum(respect[priorite]) / len(respect[priorite])
        print(f"  {priorite} : {serie[len(serie) // 2]:7.1f} h, {taux:.0%} ({len(serie)} bons)")
    medianes = [sorted(delais[p])[len(delais[p]) // 2] for p in ("P1", "P2", "P3", "P4")]
    verifier(medianes == sorted(medianes), "la médiane de fermeture augmente de P1 à P4")
    verifier(all(0.5 <= sum(r) / len(r) <= 0.95 for r in respect.values()), "respect des cibles plausible (50 à 95 %)")

    cout = defaultdict(float)
    for bon in valides:
        cout[bon["categorie"]] += sum(nombre(bon[c]) for c in ("cout_main_oeuvre", "cout_pieces", "cout_sous_traitance"))
    moyenne = {c: cout[c] / sum(1 for b in valides if b["categorie"] == c) for c in cout}
    print("\nCoût moyen par catégorie :", {c: round(v) for c, v in moyenne.items()})
    verifier(max(moyenne, key=moyenne.get) == "Dégât d'eau", "le dégât d'eau est la catégorie la plus chère par bon")

    # Récidive : même local et même catégorie dans les 30 jours (bons correctifs)
    groupes = defaultdict(list)
    for bon in bons:
        if bon["type_bon"] == "Correctif" and bon["statut"] != "Annulé":
            groupes[(bon["local_id"], bon["categorie"])].append(bon["creation"])
    recidives = total = 0
    for dates in groupes.values():
        dates.sort()
        total += len(dates)
        recidives += sum(1 for a, b in zip(dates, dates[1:]) if b - a <= timedelta(days=30))
    taux = recidives / total
    print(f"\nTaux de récidive à 30 jours : {taux:.1%}")
    verifier(0.05 <= taux <= 0.25, "taux de récidive plausible (5 à 25 %)")

    statuts = Counter(b["statut"] for b in bons)
    print("\nStatuts :", dict(statuts))
    verifier(len(statuts) == 5, "les 5 statuts sont présents")
    verifier(4000 <= len(bons) <= 12000, f"volume de « quelques milliers » de bons ({len(bons)})")

    print(f"\n{len(echecs)} échec(s)")
    sys.exit(1 if echecs else 0)


if __name__ == "__main__":
    main()
