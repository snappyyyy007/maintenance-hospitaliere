#!/usr/bin/env python3
"""Exécute les notebooks avec Spark en local, puis recoupe les tables gold.

Les résultats attendus sont recalculés ici en Python pur à partir des CSV et du manifeste :
une erreur de logique dans les notebooks ne peut donc pas se confirmer elle-même.
Les tables sont écrites en Parquet, car Delta exigerait un paquet de plus en local.

Prérequis : pyspark 3.5 et une JVM (JAVA_HOME). Usage : python scripts/tester_notebooks.py
"""
import csv
import json
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from pyspark.sql import SparkSession

RACINE = Path(__file__).resolve().parent.parent
BRUT = RACINE / "data" / "brut"
NOTEBOOKS = ["01_bronze", "02_silver", "03_gold"]
SEUIL_HEURES = 200
echecs = []


def verifier(condition, message):
    print(("OK    " if condition else "ÉCHEC ") + message)
    if not condition:
        echecs.append(message)


def executer(spark, nom, chemin=BRUT):
    print(f"\n=== {nom} ===")
    contexte = {"spark": spark, "display": lambda df: df.show(10, truncate=60)}
    notebook = json.loads((RACINE / "notebooks" / f"{nom}.ipynb").read_text(encoding="utf-8"))
    for cellule in notebook["cells"]:
        if cellule["cell_type"] != "code":
            continue
        exec("".join(cellule["source"]), contexte)
        if "parameters" in cellule["metadata"].get("tags", []):
            contexte.update(FORMAT_TABLE="parquet", CHEMIN_BRUT=str(chemin))


def tester_fichiers_defectueux(spark):
    """Un fichier vide ou incomplet doit arrêter le pipeline sans toucher aux tables existantes."""
    print("\n=== Fichiers défectueux ===")
    lignes_avant = spark.table("bronze_bons_de_travail").count()
    entete = (BRUT / "bons_de_travail.csv").read_text(encoding="utf-8").splitlines()[0]
    cas = {
        "sans ligne": ("bons_de_travail.csv", entete + "\n"),
        "vide": ("priorites.csv", ""),
        "colonne manquante": ("pavillons.csv", "nom,vocation\nA,B\n"),
    }
    for nom, (fichier, contenu) in cas.items():
        dossier = Path(tempfile.mkdtemp(prefix="brut-"))
        for source in BRUT.glob("*.csv"):
            shutil.copy(source, dossier)
        (dossier / fichier).write_text(contenu, encoding="utf-8")
        try:
            executer(spark, "01_bronze", dossier)
            arrete = False
        except Exception as erreur:
            arrete = True
            print(f"      arrêt : {str(erreur).splitlines()[0][:90]}")
        shutil.rmtree(dossier, ignore_errors=True)
        verifier(arrete, f"fichier {nom} : le notebook bronze échoue")
    verifier(spark.table("bronze_bons_de_travail").count() == lignes_avant, "les tables bronze sont restées intactes")


def lire_date(texte):
    if not texte:
        return None
    gabarit = "%d/%m/%Y %H:%M" if texte[2] == "/" else "%Y-%m-%d %H:%M:%S"
    return datetime.strptime(texte, gabarit)


def nombre(texte):
    return float(texte) if texte else 0.0


def attendus():
    """Refait le nettoyage sans Spark, ligne par ligne."""
    with open(BRUT / "bons_de_travail.csv", encoding="utf-8", newline="") as fichier:
        brut = list(csv.DictReader(fichier))
    uniques = list({tuple(l.items()): l for l in brut}.values())

    valides, quarantaine = [], []
    for bon in uniques:
        creation, fermeture = lire_date(bon["date_creation"]), lire_date(bon["date_fermeture"])
        motifs = []
        if fermeture and fermeture < creation:
            motifs.append("fermeture_avant_creation")
        if nombre(bon["cout_pieces"]) < 0:
            motifs.append("cout_negatif")
        if nombre(bon["heures_main_oeuvre"]) > SEUIL_HEURES:
            motifs.append("heures_aberrantes")
        bon["creation"], bon["fermeture"] = creation, fermeture
        (quarantaine if motifs else valides).append((bon, motifs))

    valides = [b for b, _ in valides]
    fermes = [b for b in valides if b["statut"] == "Fermé"]
    cout = sum(nombre(b["cout_main_oeuvre"]) + nombre(b["cout_pieces"]) + nombre(b["cout_sous_traitance"]) for b in fermes)

    # La catégorie n'a pas besoin d'être normalisée ici si on la ramène à sa première lettre utile
    familles = {"p": "plomberie", "e": "electricite", "é": "electricite", "c": "cvc", "h": "cvc", "d": "eau"}
    groupes = defaultdict(list)
    admissibles = [b for b in valides if b["type_bon"] == "Correctif" and b["statut"] != "Annulé"]
    for bon in admissibles:
        cle = (bon["local_id"], familles[bon["categorie"].strip().lower()[0]])
        groupes[cle].append((bon["creation"], bon["bon_id"]))
    recidives = 0
    for dates in groupes.values():
        dates.sort()
        recidives += sum(1 for a, b in zip(dates, dates[1:]) if b[0] - a[0] <= timedelta(days=30))

    return {
        "lignes_brutes": len(brut), "uniques": len(uniques), "valides": len(valides),
        "quarantaine": quarantaine, "cout_total": cout, "admissibles": len(admissibles), "recidives": recidives,
        "pieces_manquantes": sum(1 for b in fermes if not b["cout_pieces"]),
        "techniciens_inconnus": sum(1 for b in fermes if not b["technicien_id"]),
    }


def recouper(spark):
    print("\n=== Recoupement ===")
    manifeste = json.loads((RACINE / "data" / "manifeste.json").read_text(encoding="utf-8"))
    defauts = manifeste["defauts_injectes"]
    attendu = attendus()

    def valeur(requete):
        return spark.sql(requete).first()[0]

    verifier(spark.table("bronze_bons_de_travail").count() == attendu["lignes_brutes"], "bronze garde toutes les lignes brutes")
    silver = spark.table("silver_bons_de_travail").count()
    quarantaine = spark.table("silver_quarantaine").count()
    verifier(silver + quarantaine == attendu["uniques"], f"silver {silver} + quarantaine {quarantaine} = {attendu['uniques']} bons uniques")
    verifier(silver == attendu["valides"], f"{silver} bons valides, comme le calcul indépendant")

    for motif, cle in [("fermeture_avant_creation", "fermetures_avant_creation"),
                       ("cout_negatif", "couts_negatifs"), ("heures_aberrantes", "heures_aberrantes")]:
        nombre_spark = valeur(f"SELECT count(*) FROM silver_quarantaine WHERE motifs LIKE '%{motif}%'")
        verifier(nombre_spark == defauts[cle], f"{motif} : {nombre_spark} (manifeste : {defauts[cle]})")

    categories = {r[0] for r in spark.sql("SELECT DISTINCT categorie FROM silver_bons_de_travail").collect()}
    verifier(categories == {"Plomberie", "Électricité", "CVC", "Dégât d'eau"}, "4 catégories propres")
    verifier(valeur("SELECT count(DISTINCT pavillon_id) FROM silver_bons_de_travail") == 7, "7 pavillons propres")
    verifier(valeur("SELECT count(*) FROM silver_bons_de_travail WHERE date_creation IS NULL") == 0, "date_creation toujours lisible")
    verifier(valeur("SELECT count(*) FROM silver_bons_de_travail WHERE statut = 'Fermé' AND date_fermeture IS NULL") == 0,
             "tous les bons fermés ont une date de fermeture")
    verifier(valeur("SELECT count(*) FROM silver_bons_de_travail WHERE cout_pieces_manquant") == attendu["pieces_manquantes"],
             f"{attendu['pieces_manquantes']} coûts de pièces signalés")
    verifier(valeur("SELECT count(*) FROM silver_bons_de_travail WHERE technicien_id = 'INCONNU'") == attendu["techniciens_inconnus"],
             f"{attendu['techniciens_inconnus']} techniciens INCONNU")

    cout = valeur("SELECT sum(cout_total) FROM gold_fait_bons")
    verifier(abs(cout - attendu["cout_total"]) < 1, f"coût total {cout:,.2f} $ identique au calcul indépendant")
    agg = valeur("SELECT sum(cout_total) FROM gold_agg_mensuelle")
    verifier(abs(agg - cout) < 1, "gold_agg_mensuelle retrouve le coût total")

    recidives = valeur("SELECT sum(est_recidive) FROM gold_fait_bons")
    admissibles = valeur("SELECT sum(est_admissible_recidive) FROM gold_fait_bons")
    verifier(recidives == attendu["recidives"], f"{recidives} récidives, comme le calcul indépendant")
    verifier(admissibles == attendu["admissibles"], f"{admissibles} bons admissibles, comme le calcul indépendant")
    print(f"      taux de récidive : {recidives / admissibles:.1%}")

    trouves = {(r.local_id, r.categorie) for r in spark.table("gold_points_chauds").collect()}
    caches = {(p["local_id"], p["categorie"]) for p in manifeste["points_chauds"] if p["occurrences"] >= 6}
    verifier(caches <= trouves, f"{len(caches & trouves)} points chauds cachés sur {len(caches)} retrouvés")
    print(f"      {len(trouves)} points chauds au total, dont {len(trouves - caches)} non prévus")

    bornes = spark.sql("SELECT min(date), max(date), count(*) FROM gold_dim_date").first()
    verifier(str(bornes[0]).endswith("-01-01") and str(bornes[1]).endswith("-12-31"), f"dim_date : années complètes ({bornes[2]} jours)")
    samedi = valeur("SELECT jour_semaine_nom FROM gold_dim_date WHERE date = DATE'2026-01-03'")
    verifier(samedi == "samedi", "dim_date : le 3 janvier 2026 est un samedi")


def controler_dax(spark):
    """DAX ne s'exécute pas ici : on vérifie au moins chaque référence du fichier de mesures."""
    print("\n=== Mesures DAX ===")
    texte = (RACINE / "powerbi" / "mesures.dax").read_text(encoding="utf-8")
    code = "\n".join(l for l in texte.splitlines() if not l.strip().startswith("//"))
    sans_chaines = re.sub(r'"[^"]*"', '""', code)
    mesures = set(re.findall(r"MEASURE\s+gold_fait_bons\[([^\]]+)\]", code))

    verifier(sans_chaines.count("(") == sans_chaines.count(")"), "parenthèses équilibrées")
    colonnes = {}
    inconnues = []
    for table, nom in set(re.findall(r"\b((?:gold|silver)_\w+)\[([^\]]+)\]", code)):
        colonnes.setdefault(table, spark.table(table).columns)
        if nom not in colonnes[table] and nom not in mesures:
            inconnues.append(f"{table}[{nom}]")
    verifier(not inconnues, f"toutes les colonnes existent {inconnues or ''}")
    appels = set(re.findall(r"(?<![\w\]])\[([^\]]+)\]", code))
    verifier(appels <= mesures, f"{len(mesures)} mesures, toutes les références sont définies {sorted(appels - mesures) or ''}")

    definitions = code.split("EVALUATE")[0]
    valeurs = {r[0] for r in spark.sql("SELECT statut FROM gold_fait_bons UNION SELECT categorie FROM gold_fait_bons").collect()}
    litteraux = set(re.findall(r'"([^"]*)"', definitions))
    verifier(litteraux <= valeurs, f"les textes comparés existent dans les données {sorted(litteraux - valeurs) or ''}")


def ecrire_valeurs_attendues(spark):
    """Calcule chaque mesure en SQL, pour la comparer ensuite aux résultats DAX dans Fabric."""
    expressions = {
        "Nb bons": "count_if(statut <> 'Annulé')",
        "Nb bons fermés": "sum(est_ferme)",
        "Nb bons ouverts": "count_if(statut IN ('Ouvert', 'Assigné', 'En cours'))",
        "Coût total": "sum(cout_total)",
        "Coût moyen par bon fermé": "sum(cout_total) / sum(est_ferme)",
        "Délai moyen de fermeture (h)": "avg(delai_fermeture_heures)",
        "Délai médian de fermeture (h)": "percentile(delai_fermeture_heures, 0.5)",
        "Taux de respect de la cible": "avg(est_cible_respectee)",
        "Nb récidives": "sum(est_recidive)",
        "Taux de récidive": "sum(est_recidive) / sum(est_admissible_recidive)",
        "Coût des récidives": "sum(CASE WHEN est_recidive = 1 THEN cout_total END)",
        "Part du coût en récidive": "sum(CASE WHEN est_recidive = 1 THEN cout_total END) / sum(cout_total)",
        "Nb dégâts d'eau": "count_if(categorie = \"Dégât d'eau\" AND statut <> 'Annulé')",
        "Coût des dégâts d'eau": "sum(CASE WHEN categorie = \"Dégât d'eau\" THEN cout_total END)",
    }
    selection = ", ".join(f"{sql} AS `m{i}`" for i, sql in enumerate(expressions.values()))
    superficies = {r.pavillon_id: r.superficie_m2 for r in spark.table("gold_dim_pavillon").collect()}

    def mesurer(contexte, condition, superficie=None):
        ligne = spark.sql(f"SELECT {selection} FROM gold_fait_bons WHERE {condition}").first()
        valeurs = dict(zip(expressions, ligne))
        if superficie:
            valeurs["Coût par m²"] = valeurs["Coût total"] / superficie
            valeurs["Dégâts d'eau par 10 000 m²"] = valeurs["Nb dégâts d'eau"] / superficie * 10000
        return [(contexte, mesure, round(float(v), 4)) for mesure, v in valeurs.items()]

    lignes = mesurer("Global", "1 = 1", sum(superficies.values()))
    lignes.append(("Global", "Nb bons en quarantaine", float(spark.table("silver_quarantaine").count())))
    for pavillon, superficie in sorted(superficies.items()):
        lignes += mesurer(pavillon, f"pavillon_id = '{pavillon}'", superficie)

    annee = mesurer("Année 2026", "year(date_creation_jour) = 2026")
    n_1 = spark.sql("""
        SELECT count_if(statut <> 'Annulé') FROM gold_fait_bons
        WHERE date_creation_jour BETWEEN '2025-01-01'
          AND add_months((SELECT max(date_creation_jour) FROM gold_fait_bons), -12)
    """).first()[0]
    nb_bons = next(v for _, m, v in annee if m == "Nb bons")
    cout = next(v for _, m, v in annee if m == "Coût total")
    fermes = spark.sql("SELECT sum(est_ferme) FROM gold_fait_bons WHERE year(date_fermeture_jour) = 2026").first()[0]
    annee += [("Année 2026", "Nb bons N-1", float(n_1)),
              ("Année 2026", "Variation annuelle des bons", round((nb_bons - n_1) / n_1, 4)),
              ("Année 2026", "Coût cumulé annuel", cout),
              ("Année 2026", "Bons fermés par date de fermeture", float(fermes))]
    lignes += annee

    with open(RACINE / "powerbi" / "valeurs_attendues.csv", "w", newline="", encoding="utf-8") as fichier:
        ecrivain = csv.writer(fichier)
        ecrivain.writerow(["contexte", "mesure", "valeur"])
        ecrivain.writerows(lignes)
    print(f"\n{len(lignes)} valeurs attendues écrites dans powerbi/valeurs_attendues.csv")


def main():
    entrepot = tempfile.mkdtemp(prefix="entrepot-")
    spark = (
        SparkSession.builder.master("local[4]")
        .config("spark.sql.warehouse.dir", entrepot)
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.session.timeZone", "UTC")  # comme Fabric ; évite les trous de l'heure d'été
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    try:
        # Deux passages, comme deux exécutions planifiées du pipeline sur le même lakehouse
        for nom in NOTEBOOKS * 2:
            executer(spark, nom)
        tester_fichiers_defectueux(spark)
        recouper(spark)
        controler_dax(spark)
        ecrire_valeurs_attendues(spark)
    finally:
        spark.stop()
        shutil.rmtree(entrepot, ignore_errors=True)
    print(f"\n{len(echecs)} échec(s)")
    sys.exit(1 if echecs else 0)


if __name__ == "__main__":
    main()
